from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import IntEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from qka_shared.acl import SourceAclState
from qka_shared.allowlist import ACCOUNT_ENABLED, LINKED, PROVIDER_AD, PROVIDER_GOOGLE, USER_ACTIVE
from qka_shared.models import (
    AccessPolicy,
    AdGroupMembership,
    AdSecurityGroup,
    Department,
    DepartmentMembership,
    ExternalIdentity,
    QkaUser,
)


class AccessDecision(IntEnum):
    DENY = 0
    ALLOW = 1


class AccessEffect(IntEnum):
    GRANT = 0
    RESTRICT = 1


class AccessScopeKind(IntEnum):
    DOCUMENT = 0
    CONTAINER = 1
    OWNER_SCOPE = 2
    DEPARTMENT_CORPUS = 3


class AccessTargetKind(IntEnum):
    QKA_USER = 0
    AD_GROUP = 1
    DEPARTMENT = 2
    EXECUTIVE_SET = 3


class PolicyStatus(IntEnum):
    ACTIVE = 0
    SUPERSEDED = 1


@dataclass(frozen=True)
class ResourceContext:
    resource_id: uuid.UUID | None
    resource_owner_user_id: uuid.UUID | None
    approved_indexed: bool
    classification_known: bool
    restricted: bool
    content_verified: bool
    source_acl: SourceAclState
    resource_department_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)


def has_verified_linked_identity(user: QkaUser) -> bool:
    return any(
        identity.link_status == LINKED
        and identity.account_status == ACCOUNT_ENABLED
        and identity.provider in (PROVIDER_AD, PROVIDER_GOOGLE)
        for identity in user.identities
    )


def decide(session: Session, caller: QkaUser, resource: ResourceContext) -> AccessDecision:
    if caller.status != USER_ACTIVE or not caller.login_enabled:
        return AccessDecision.DENY
    if not has_verified_linked_identity(caller):
        return AccessDecision.DENY
    if (
        not resource.classification_known
        or resource.restricted
        or not resource.approved_indexed
        or not resource.content_verified
        or resource.source_acl != SourceAclState.ALLOWED
    ):
        return AccessDecision.DENY

    department_ids = load_department_ids(session, caller)
    group_ids = load_group_ids(session, caller.id)
    policies = session.execute(select(AccessPolicy).where(AccessPolicy.status == PolicyStatus.ACTIVE)).scalars().all()

    if any(
        policy.effect == AccessEffect.RESTRICT
        and policy_applies(policy, caller.id, department_ids, group_ids, resource)
        for policy in policies
    ):
        return AccessDecision.DENY

    owner_allow = resource.resource_owner_user_id == caller.id
    individual = has_grant(policies, caller.id, department_ids, group_ids, resource, AccessTargetKind.QKA_USER, caller.id)
    department_grant = any(
        has_grant(policies, caller.id, department_ids, group_ids, resource, AccessTargetKind.DEPARTMENT, dept_id)
        for dept_id in department_ids
    )
    group_grant = any(
        has_grant(policies, caller.id, department_ids, group_ids, resource, AccessTargetKind.AD_GROUP, group_id)
        for group_id in group_ids
    )
    if owner_allow or individual or department_grant or group_grant:
        return AccessDecision.ALLOW
    return AccessDecision.DENY


def policy_applies(
    policy: AccessPolicy,
    caller_id: uuid.UUID,
    department_ids: set[uuid.UUID],
    group_ids: set[uuid.UUID],
    resource: ResourceContext,
) -> bool:
    if policy.target_kind == AccessTargetKind.QKA_USER:
        target_matches = policy.target_id == caller_id
    elif policy.target_kind == AccessTargetKind.DEPARTMENT:
        target_matches = policy.target_id in department_ids
    elif policy.target_kind == AccessTargetKind.AD_GROUP:
        target_matches = policy.target_id in group_ids
    else:
        target_matches = False
    if not target_matches:
        return False
    if policy.scope_kind == AccessScopeKind.OWNER_SCOPE:
        return policy.owner_qka_user_id is not None and policy.owner_qka_user_id == resource.resource_owner_user_id
    if policy.scope_kind in (AccessScopeKind.DOCUMENT, AccessScopeKind.CONTAINER):
        return policy.resource_id is not None and policy.resource_id == resource.resource_id
    if policy.scope_kind == AccessScopeKind.DEPARTMENT_CORPUS:
        return (
            policy.target_kind == AccessTargetKind.DEPARTMENT
            and policy.target_id in resource.resource_department_ids
        )
    return False


def has_grant(
    policies: list[AccessPolicy],
    caller_id: uuid.UUID,
    department_ids: set[uuid.UUID],
    group_ids: set[uuid.UUID],
    resource: ResourceContext,
    kind: AccessTargetKind,
    target_id: uuid.UUID,
) -> bool:
    return any(
        policy.effect == AccessEffect.GRANT
        and policy.target_kind == kind
        and policy.target_id == target_id
        and policy_applies(policy, caller_id, department_ids, group_ids, resource)
        for policy in policies
    )


def load_department_ids(session: Session, caller: QkaUser) -> set[uuid.UUID]:
    ids: set[uuid.UUID] = set()
    if caller.effective_department_id:
        dept = session.get(Department, caller.effective_department_id)
        if dept is not None and dept.status == 0:
            ids.add(dept.id)
    rows = session.execute(
            select(DepartmentMembership.department_id)
            .join(Department, Department.id == DepartmentMembership.department_id)
            .where(DepartmentMembership.qka_user_id == caller.id, Department.status == 0)
        ).all()
    ids.update(row[0] for row in rows)
    return ids


def load_group_ids(session: Session, caller_id: uuid.UUID) -> set[uuid.UUID]:
    rows = session.execute(
            select(AdSecurityGroup.id)
            .join(AdGroupMembership, AdGroupMembership.ad_security_group_id == AdSecurityGroup.id)
            .join(ExternalIdentity, ExternalIdentity.id == AdGroupMembership.external_identity_id)
            .where(
                ExternalIdentity.qka_user_id == caller_id,
                ExternalIdentity.link_status == LINKED,
                ExternalIdentity.account_status == ACCOUNT_ENABLED,
                AdSecurityGroup.is_active.is_(True),
            )
        ).all()
    return {row[0] for row in rows}
