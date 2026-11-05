"""Active Directory connection, diagnostics, sync, and identity mapping."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from qka_api.ldap_client import (
    AUTH_LDAPS,
    AUTH_WINDOWS_INTEGRATED,
    AdUserRecord,
    LdapConnectParams,
    LdapGateway,
    LdapError,
    current_windows_identity,
    domain_dns_to_base_dn,
    normalize_auth_mode,
)
from qka_shared.allowlist import ACCOUNT_ENABLED, LINKED, PROVIDER_AD, PROVIDER_GOOGLE, USER_ACTIVE
from qka_shared.chat_access import get_or_create_chat_access_for_user
from qka_shared.config import Settings
from qka_shared.email_address import try_normalize
from qka_shared.models import (
    AdAuditEvent,
    AdConnection,
    AdDirectoryGroup,
    AdDirectoryUser,
    AdGroupMemberEdge,
    AdOuScope,
    AdSyncJob,
    ExternalIdentity,
    GoogleWorkspaceAccount,
    QkaUser,
)
from qka_shared.secrets import protect_secret, unprotect_secret

STATUS_UNKNOWN = 0
STATUS_CONNECTED = 1
STATUS_FAILED = 2
STATUS_DISABLED = 3

MAP_UNLINKED = 0
MAP_PENDING = 1
MAP_LINKED = 2
MAP_CONFLICT = 3

JOB_QUEUED = 0
JOB_RUNNING = 1
JOB_COMPLETED = 2
JOB_FAILED = 3
JOB_CANCELLED = 4

_TEST_RATE: dict[str, float] = {}
_TEST_RATE_WINDOW = 3.0


class AdValidationError(Exception):
    pass


class AdConflictError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(session: Session, *, action: str, actor_id: uuid.UUID | None, connection_id: uuid.UUID | None, detail: str) -> None:
    # Never put secrets in detail.
    safe = re.sub(r"(?i)(password|secret|token)\s*[:=]\s*\S+", r"\1=[redacted]", detail or "")[:2000]
    session.add(
        AdAuditEvent(
            id=uuid.uuid4(),
            connection_id=connection_id,
            action=action,
            actor_user_id=actor_id,
            detail=safe,
            created_at=_now(),
        )
    )


def _rate_limit_test(key: str) -> None:
    last = _TEST_RATE.get(key, 0.0)
    now = time.monotonic()
    if now - last < _TEST_RATE_WINDOW:
        raise AdValidationError("Connection tests are rate-limited. Wait a few seconds and try again.")
    _TEST_RATE[key] = now


def connection_dto(row: AdConnection) -> dict:
    auth_mode = normalize_auth_mode(row.protocol)
    return {
        "id": str(row.id),
        "connectionName": row.connection_name,
        "domainDnsName": row.domain_dns_name,
        "domainController": row.domain_controller,
        "protocol": auth_mode,
        "authMode": auth_mode,
        "port": int(row.port or (389 if auth_mode == AUTH_WINDOWS_INTEGRATED else 636)),
        "baseDn": row.base_dn,
        "bindUsername": row.bind_username if auth_mode == AUTH_LDAPS else "",
        "bindSecretConfigured": bool(row.bind_secret_protected) if auth_mode == AUTH_LDAPS else False,
        "timeoutSeconds": int(row.timeout_seconds or 15),
        "verifyTls": bool(row.verify_tls) if auth_mode == AUTH_LDAPS else False,
        "connectionStatus": int(row.connection_status or 0),
        "statusLabel": _status_label(row.connection_status),
        "serviceIdentity": current_windows_identity() if auth_mode == AUTH_WINDOWS_INTEGRATED else None,
        "lastSuccessfulTestAt": row.last_successful_test_at.isoformat() if row.last_successful_test_at else None,
        "lastSafeError": row.last_safe_error,
        "lastSuccessfulSyncAt": row.last_successful_sync_at.isoformat() if row.last_successful_sync_at else None,
        "lastSyncResult": row.last_sync_result,
        "currentSyncJobId": str(row.current_sync_job_id) if row.current_sync_job_id else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def _status_label(status: int | None) -> str:
    if status == STATUS_CONNECTED:
        return "connected"
    if status == STATUS_FAILED:
        return "failed"
    if status == STATUS_DISABLED:
        return "disabled"
    return "not_configured"


def list_connections(session: Session) -> list[dict]:
    rows = session.execute(select(AdConnection).order_by(AdConnection.connection_name)).scalars().all()
    return [connection_dto(row) for row in rows]


def get_connection(session: Session, connection_id: uuid.UUID) -> AdConnection:
    row = session.get(AdConnection, connection_id)
    if row is None:
        raise AdValidationError("Active Directory connection was not found.")
    return row


def _params_from_body(body: dict, *, existing: AdConnection | None, settings: Settings) -> LdapConnectParams:
    raw_mode = body.get("authMode")
    if raw_mode is None or str(raw_mode).strip() == "":
        raw_mode = body.get("protocol")
    if raw_mode is None or str(raw_mode).strip() == "":
        raw_mode = existing.protocol if existing else AUTH_WINDOWS_INTEGRATED
    try:
        auth_mode = normalize_auth_mode(str(raw_mode))
    except LdapError as exc:
        raise AdValidationError(str(exc)) from exc

    domain_dns = str(body.get("domainDnsName") or (existing.domain_dns_name if existing else "")).strip()
    domain_controller = str(
        body.get("domainController") or (existing.domain_controller if existing else "")
    ).strip()
    base_dn = str(body.get("baseDn") or (existing.base_dn if existing else "")).strip()
    timeout = max(
        5,
        min(int(body.get("timeoutSeconds") or (existing.timeout_seconds if existing else 15) or 15), 60),
    )

    if auth_mode == AUTH_WINDOWS_INTEGRATED:
        # Process identity + LDAP signing/sealing. No bind secret. Port 389 (not LDAPS).
        port = int(body.get("port") or (existing.port if existing and existing.protocol == AUTH_WINDOWS_INTEGRATED else 389) or 389)
        if port == 636:
            port = 389
        if not base_dn and domain_dns:
            base_dn = domain_dns_to_base_dn(domain_dns)
        return LdapConnectParams(
            domain_dns_name=domain_dns,
            domain_controller=domain_controller,
            port=port,
            bind_username="",
            bind_password="",
            base_dn=base_dn,
            timeout_seconds=timeout,
            verify_tls=False,
            auth_mode=AUTH_WINDOWS_INTEGRATED,
        )

    # Optional LDAPS path — certificate validation required; no silent downgrade.
    verify = body.get("verifyTls")
    if verify is None:
        verify = existing.verify_tls if existing else True
    if not verify:
        raise AdValidationError("TLS certificate validation is required for LDAPS.")
    password = body.get("bindPassword") or body.get("password")
    if password is None or password == "":
        if existing is None or normalize_auth_mode(existing.protocol) != AUTH_LDAPS:
            raise AdValidationError("Bind password is required for LDAPS.")
        password = unprotect_secret(settings, existing.bind_secret_protected) or ""
        if not password:
            raise AdValidationError("Stored bind password could not be decrypted. Re-enter the password.")
    port = int(body.get("port") or (existing.port if existing else 636) or 636)
    return LdapConnectParams(
        domain_dns_name=domain_dns,
        domain_controller=domain_controller,
        port=port,
        bind_username=str(body.get("bindUsername") or (existing.bind_username if existing else "")).strip(),
        bind_password=str(password),
        base_dn=base_dn,
        timeout_seconds=timeout,
        verify_tls=True,
        auth_mode=AUTH_LDAPS,
    )


def test_connection_payload(
    session: Session,
    settings: Settings,
    gateway: LdapGateway,
    actor_id: uuid.UUID,
    body: dict,
    *,
    connection_id: uuid.UUID | None = None,
    persist_on_row: bool = False,
) -> dict:
    _rate_limit_test(f"{actor_id}:{connection_id or 'new'}")
    existing = session.get(AdConnection, connection_id) if connection_id else None
    params = _params_from_body(body, existing=existing, settings=settings)
    if not params.domain_dns_name and not params.domain_controller:
        raise AdValidationError("Domain DNS name or domain controller is required.")
    if params.auth_mode == AUTH_LDAPS and not params.bind_username:
        raise AdValidationError("Bind username is required for LDAPS.")
    succeeded, safe_error, checks, discovered = gateway.diagnose(params)
    identity = current_windows_identity() if params.auth_mode == AUTH_WINDOWS_INTEGRATED else None
    check_map = {c.name: c.status for c in checks}
    # Only true when negotiation actually succeeded — never imply PASS after Kerberos failure.
    signing_ok = check_map.get("signing") == "pass" or (
        params.auth_mode == AUTH_LDAPS and check_map.get("tls") == "pass"
    )
    sealing_ok = check_map.get("sealing") == "pass" or (
        params.auth_mode == AUTH_LDAPS and check_map.get("tls") == "pass"
    )
    payload = {
        "succeeded": succeeded,
        "safeError": safe_error,
        "discoveredBaseDn": discovered or None,
        "authMode": params.auth_mode,
        "serviceIdentity": identity,
        "ldapSigning": bool(signing_ok),
        "ldapSealing": bool(sealing_ok),
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks],
    }
    if persist_on_row and existing is not None:
        existing.last_diagnostics_json = json.dumps(payload["checks"])
        existing.last_safe_error = safe_error
        if succeeded:
            existing.connection_status = STATUS_CONNECTED
            existing.last_successful_test_at = _now()
            if discovered and not existing.base_dn:
                existing.base_dn = discovered
        else:
            existing.connection_status = STATUS_FAILED
        existing.updated_at = _now()
        existing.updated_by_user_id = actor_id
        _audit(
            session,
            action="connection_test",
            actor_id=actor_id,
            connection_id=existing.id,
            detail=f"succeeded={succeeded}; error={safe_error or ''}",
        )
        session.flush()
    else:
        _audit(
            session,
            action="connection_test",
            actor_id=actor_id,
            connection_id=connection_id,
            detail=f"succeeded={succeeded}; ephemeral={not persist_on_row}",
        )
    return payload


def save_connection(
    session: Session,
    settings: Settings,
    gateway: LdapGateway,
    actor_id: uuid.UUID,
    body: dict,
    *,
    connection_id: uuid.UUID | None = None,
) -> dict:
    """Validate auth (Windows Integrated or LDAPS) first; only then persist. Never returns secrets."""
    existing = session.get(AdConnection, connection_id) if connection_id else None
    if existing and existing.connection_status == STATUS_DISABLED:
        raise AdValidationError("Re-enable the connection before saving changes.")
    params = _params_from_body(body, existing=existing, settings=settings)
    name = str(body.get("connectionName") or (existing.connection_name if existing else "") or "Active Directory").strip()
    # Must pass diagnostics before save.
    result = test_connection_payload(
        session, settings, gateway, actor_id, body, connection_id=connection_id, persist_on_row=False
    )
    if not result["succeeded"]:
        raise AdValidationError(result.get("safeError") or "Connection test failed. Connection was not saved.")
    discovered = result.get("discoveredBaseDn") or ""
    base_dn = (params.base_dn or discovered or "").strip()
    now = _now()
    if existing is None:
        row = AdConnection(
            id=uuid.uuid4(),
            connection_name=name,
            domain_dns_name=params.domain_dns_name,
            domain_controller=params.domain_controller,
            protocol=params.auth_mode,
            port=params.port,
            base_dn=base_dn,
            bind_username=params.bind_username if params.auth_mode == AUTH_LDAPS else "",
            bind_secret_protected=(
                protect_secret(settings, params.bind_password) if params.auth_mode == AUTH_LDAPS else None
            ),
            timeout_seconds=params.timeout_seconds,
            verify_tls=params.auth_mode == AUTH_LDAPS,
            connection_status=STATUS_CONNECTED,
            last_successful_test_at=now,
            last_safe_error=None,
            last_diagnostics_json=json.dumps(result["checks"]),
            created_at=now,
            updated_at=now,
            created_by_user_id=actor_id,
            updated_by_user_id=actor_id,
        )
        session.add(row)
        _audit(
            session,
            action="connection_create",
            actor_id=actor_id,
            connection_id=row.id,
            detail=f"name={name}; auth={params.auth_mode}",
        )
    else:
        row = existing
        row.connection_name = name
        row.domain_dns_name = params.domain_dns_name
        row.domain_controller = params.domain_controller
        row.protocol = params.auth_mode
        row.port = params.port
        row.base_dn = base_dn
        if params.auth_mode == AUTH_LDAPS:
            row.bind_username = params.bind_username
            if body.get("bindPassword") or body.get("password"):
                row.bind_secret_protected = protect_secret(settings, params.bind_password)
            row.verify_tls = True
        else:
            row.bind_username = ""
            row.bind_secret_protected = None
            row.verify_tls = False
        row.timeout_seconds = params.timeout_seconds
        row.connection_status = STATUS_CONNECTED
        row.last_successful_test_at = now
        row.last_safe_error = None
        row.last_diagnostics_json = json.dumps(result["checks"])
        row.updated_at = now
        row.updated_by_user_id = actor_id
        _audit(
            session,
            action="connection_update",
            actor_id=actor_id,
            connection_id=row.id,
            detail=f"name={name}; auth={params.auth_mode}",
        )
    session.flush()
    return connection_dto(row)


def disable_connection(session: Session, actor_id: uuid.UUID, connection_id: uuid.UUID) -> dict:
    row = get_connection(session, connection_id)
    row.connection_status = STATUS_DISABLED
    row.updated_at = _now()
    row.updated_by_user_id = actor_id
    _audit(session, action="connection_disable", actor_id=actor_id, connection_id=row.id, detail="")
    session.flush()
    return connection_dto(row)


def enable_connection(session: Session, actor_id: uuid.UUID, connection_id: uuid.UUID) -> dict:
    row = get_connection(session, connection_id)
    if row.connection_status == STATUS_DISABLED:
        row.connection_status = STATUS_UNKNOWN
    row.updated_at = _now()
    row.updated_by_user_id = actor_id
    _audit(session, action="connection_enable", actor_id=actor_id, connection_id=row.id, detail="")
    session.flush()
    return connection_dto(row)


def delete_connection(session: Session, actor_id: uuid.UUID, connection_id: uuid.UUID) -> None:
    row = get_connection(session, connection_id)
    if row.current_sync_job_id:
        job = session.get(AdSyncJob, row.current_sync_job_id)
        if job and job.status in {JOB_QUEUED, JOB_RUNNING}:
            raise AdConflictError("Cancel the running sync before removing the connection.")
    # Directory cache for this connection only; never touch ExternalIdentity/Google.
    for model in (AdGroupMemberEdge, AdDirectoryGroup, AdDirectoryUser, AdOuScope, AdSyncJob):
        for item in session.execute(select(model).where(model.connection_id == connection_id)).scalars().all():
            session.delete(item)
    _audit(session, action="connection_delete", actor_id=actor_id, connection_id=row.id, detail=row.connection_name)
    session.delete(row)
    session.flush()


def overview(session: Session, connection_id: uuid.UUID | None = None) -> dict:
    row = None
    if connection_id:
        row = session.get(AdConnection, connection_id)
    if row is None:
        row = session.execute(select(AdConnection).order_by(AdConnection.updated_at.desc())).scalars().first()
    if row is None:
        return {
            "configured": False,
            "connection": None,
            "usersDiscovered": 0,
            "groupsDiscovered": 0,
            "selectedOus": 0,
            "linkedIdentities": 0,
            "pendingMappings": 0,
            "conflictMappings": 0,
            "disabledAccounts": 0,
        }
    users = session.scalar(
        select(func.count()).select_from(AdDirectoryUser).where(
            AdDirectoryUser.connection_id == row.id, AdDirectoryUser.is_present.is_(True)
        )
    ) or 0
    groups = session.scalar(
        select(func.count()).select_from(AdDirectoryGroup).where(
            AdDirectoryGroup.connection_id == row.id, AdDirectoryGroup.is_present.is_(True)
        )
    ) or 0
    ous = session.scalar(
        select(func.count()).select_from(AdOuScope).where(
            AdOuScope.connection_id == row.id, AdOuScope.selected.is_(True)
        )
    ) or 0
    linked = session.scalar(
        select(func.count()).select_from(AdDirectoryUser).where(
            AdDirectoryUser.connection_id == row.id, AdDirectoryUser.mapping_status == MAP_LINKED
        )
    ) or 0
    pending = session.scalar(
        select(func.count()).select_from(AdDirectoryUser).where(
            AdDirectoryUser.connection_id == row.id, AdDirectoryUser.mapping_status == MAP_PENDING
        )
    ) or 0
    conflicts = session.scalar(
        select(func.count()).select_from(AdDirectoryUser).where(
            AdDirectoryUser.connection_id == row.id, AdDirectoryUser.mapping_status == MAP_CONFLICT
        )
    ) or 0
    disabled = session.scalar(
        select(func.count()).select_from(AdDirectoryUser).where(
            AdDirectoryUser.connection_id == row.id,
            AdDirectoryUser.is_present.is_(True),
            AdDirectoryUser.account_enabled.is_(False),
        )
    ) or 0
    return {
        "configured": True,
        "connection": connection_dto(row),
        "usersDiscovered": int(users),
        "groupsDiscovered": int(groups),
        "selectedOus": int(ous),
        "linkedIdentities": int(linked),
        "pendingMappings": int(pending),
        "conflictMappings": int(conflicts),
        "disabledAccounts": int(disabled),
        "warnings": (
            ["Some AD accounts are disabled — review authorization impact."] if disabled else []
        ),
    }


def integrations_hub_status(session: Session) -> dict:
    """Safe status cards for Integrations overview (no secrets)."""
    from qka_api.services import synology as syn_svc
    from qka_shared.models import SynologyConnectionSettings

    ad = overview(session)
    syn_rows = session.execute(select(SynologyConnectionSettings)).scalars().all()
    syn_ok = any(int(r.connection_status or 0) == syn_svc.STATUS_CONNECTED for r in syn_rows)
    syn_last = None
    for r in syn_rows:
        if r.last_successful_test_at and (syn_last is None or r.last_successful_test_at > syn_last):
            syn_last = r.last_successful_test_at
    return {
        "synology": {
            "status": "connected" if syn_ok else ("configured" if syn_rows else "not_configured"),
            "lastSuccessfulCheck": syn_last.isoformat() if syn_last else None,
        },
        "activeDirectory": {
            "status": ad["connection"]["statusLabel"] if ad.get("connection") else "not_configured",
            "lastSuccessfulCheck": (ad.get("connection") or {}).get("lastSuccessfulTestAt"),
        },
        "googleWorkspace": {
            "status": "configured",
            "lastSuccessfulCheck": None,
            "note": "Primary QKA login; manage allowlist under Access.",
        },
        "chatgptMcp": {
            "status": "configured",
            "lastSuccessfulCheck": None,
            "note": "Manage from Dashboard / MCP admin tools.",
        },
    }


def _bind_params(session: Session, settings: Settings, row: AdConnection) -> LdapConnectParams:
    auth_mode = normalize_auth_mode(row.protocol)
    if auth_mode == AUTH_WINDOWS_INTEGRATED:
        return LdapConnectParams(
            domain_dns_name=row.domain_dns_name,
            domain_controller=row.domain_controller,
            port=int(row.port or 389),
            bind_username="",
            bind_password="",
            base_dn=row.base_dn,
            timeout_seconds=row.timeout_seconds,
            verify_tls=False,
            auth_mode=AUTH_WINDOWS_INTEGRATED,
        )
    secret = unprotect_secret(settings, row.bind_secret_protected)
    if not secret:
        raise AdValidationError("Bind password is not available. Edit the connection and re-enter the password.")
    return LdapConnectParams(
        domain_dns_name=row.domain_dns_name,
        domain_controller=row.domain_controller,
        port=row.port,
        bind_username=row.bind_username,
        bind_password=secret,
        base_dn=row.base_dn,
        timeout_seconds=row.timeout_seconds,
        verify_tls=True,
        auth_mode=AUTH_LDAPS,
    )


def list_ou_tree(
    session: Session,
    settings: Settings,
    gateway: LdapGateway,
    connection_id: uuid.UUID,
    *,
    parent_dn: str = "",
    search: str = "",
) -> dict:
    row = get_connection(session, connection_id)
    if row.connection_status == STATUS_DISABLED:
        raise AdValidationError("Connection is disabled.")
    params = _bind_params(session, settings, row)
    nodes = gateway.list_ous(params, parent_dn=parent_dn or row.base_dn, search=search)
    selected = {
        s.distinguished_name
        for s in session.execute(
            select(AdOuScope).where(AdOuScope.connection_id == connection_id, AdOuScope.selected.is_(True))
        ).scalars().all()
    }
    return {
        "parentDn": parent_dn or row.base_dn,
        "items": [
            {
                "name": n.name,
                "distinguishedName": n.distinguished_name,
                "selected": n.distinguished_name in selected,
            }
            for n in nodes
        ],
    }


def set_ou_selection(
    session: Session,
    actor_id: uuid.UUID,
    connection_id: uuid.UUID,
    selections: list[dict],
) -> dict:
    get_connection(session, connection_id)
    now = _now()
    existing = {
        row.distinguished_name: row
        for row in session.execute(select(AdOuScope).where(AdOuScope.connection_id == connection_id)).scalars().all()
    }
    keep: set[str] = set()
    for item in selections[:200]:
        dn = str(item.get("distinguishedName") or "").strip()
        if not dn:
            continue
        keep.add(dn)
        selected = bool(item.get("selected", True))
        name = str(item.get("name") or dn.split(",")[0])
        if dn in existing:
            existing[dn].selected = selected
            existing[dn].name = name
            existing[dn].updated_at = now
        else:
            session.add(
                AdOuScope(
                    id=uuid.uuid4(),
                    connection_id=connection_id,
                    distinguished_name=dn,
                    name=name,
                    selected=selected,
                    created_at=now,
                    updated_at=now,
                )
            )
    # Deselect removed
    for dn, row in existing.items():
        if dn not in keep:
            row.selected = False
            row.updated_at = now
    _audit(session, action="ou_scope_update", actor_id=actor_id, connection_id=connection_id, detail=f"count={len(keep)}")
    session.flush()
    return list_selected_ous(session, connection_id)


def list_selected_ous(session: Session, connection_id: uuid.UUID) -> dict:
    rows = session.execute(
        select(AdOuScope).where(AdOuScope.connection_id == connection_id, AdOuScope.selected.is_(True))
    ).scalars().all()
    return {
        "items": [
            {"id": str(r.id), "name": r.name, "distinguishedName": r.distinguished_name, "selected": True}
            for r in rows
        ]
    }


def _content_hash(user: AdUserRecord) -> str:
    blob = "|".join(
        [
            user.object_sid,
            user.sam_account_name,
            user.user_principal_name,
            user.display_name,
            user.mail,
            user.department,
            user.distinguished_name,
            "1" if user.account_enabled else "0",
        ]
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _google_email_index(session: Session) -> dict[str, tuple[uuid.UUID, str]]:
    """normalized email → (qka_user_id, email)."""
    rows = session.execute(
        select(ExternalIdentity, GoogleWorkspaceAccount)
        .join(GoogleWorkspaceAccount, GoogleWorkspaceAccount.external_identity_id == ExternalIdentity.id)
        .where(
            ExternalIdentity.provider == PROVIDER_GOOGLE,
            ExternalIdentity.link_status == LINKED,
            ExternalIdentity.qka_user_id.is_not(None),
        )
    ).all()
    index: dict[str, tuple[uuid.UUID, str]] = {}
    for identity, google in rows:
        for raw in (google.primary_email, identity.primary_email, identity.upn):
            norm, _err = try_normalize(raw or "")
            if norm and identity.qka_user_id:
                index.setdefault(norm, (identity.qka_user_id, norm))
    return index


def _match_candidates(user: AdUserRecord, email_index: dict[str, tuple[uuid.UUID, str]]) -> list[tuple[uuid.UUID, str]]:
    candidates: list[tuple[uuid.UUID, str]] = []
    seen: set[uuid.UUID] = set()
    for raw in (user.mail, user.user_principal_name):
        norm, _err = try_normalize(raw or "")
        if not norm:
            continue
        hit = email_index.get(norm)
        if hit and hit[0] not in seen:
            candidates.append(hit)
            seen.add(hit[0])
    return candidates


def start_sync(
    session: Session,
    settings: Settings,
    gateway: LdapGateway,
    actor_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    trigger: str = "manual",
) -> dict:
    row = get_connection(session, connection_id)
    if row.connection_status == STATUS_DISABLED:
        raise AdValidationError("Connection is disabled.")
    if row.connection_status != STATUS_CONNECTED:
        raise AdValidationError("Test and save a successful LDAPS connection before syncing.")
    if row.current_sync_job_id:
        running = session.get(AdSyncJob, row.current_sync_job_id)
        if running and running.status in {JOB_QUEUED, JOB_RUNNING}:
            raise AdConflictError("An Active Directory sync job is already running.")
    scopes = session.execute(
        select(AdOuScope).where(AdOuScope.connection_id == connection_id, AdOuScope.selected.is_(True))
    ).scalars().all()
    if not scopes:
        raise AdValidationError("Select at least one OU before starting synchronization.")
    job = AdSyncJob(
        id=uuid.uuid4(),
        connection_id=connection_id,
        trigger=trigger,
        status=JOB_QUEUED,
        actor_user_id=actor_id,
        created_at=_now(),
    )
    session.add(job)
    row.current_sync_job_id = job.id
    session.flush()
    _audit(session, action="sync_start", actor_id=actor_id, connection_id=connection_id, detail=f"job={job.id}")
    # Run inline for reliability in single-process deploy; tests also call run_sync_job.
    run_sync_job(session, settings, gateway, job.id)
    session.refresh(job)
    return job_dto(job)


def job_dto(job: AdSyncJob) -> dict:
    return {
        "id": str(job.id),
        "connectionId": str(job.connection_id),
        "trigger": job.trigger,
        "status": job.status,
        "usersDiscovered": job.users_discovered,
        "usersProcessed": job.users_processed,
        "usersUnchanged": job.users_unchanged,
        "usersUpdated": job.users_updated,
        "usersAdded": job.users_added,
        "groupsDiscovered": job.groups_discovered,
        "linkedCount": job.linked_count,
        "pendingCount": job.pending_count,
        "conflictCount": job.conflict_count,
        "failedCount": job.failed_count,
        "enumerationComplete": job.enumeration_complete,
        "safeError": job.safe_error,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "startedAt": job.started_at.isoformat() if job.started_at else None,
        "completedAt": job.completed_at.isoformat() if job.completed_at else None,
    }


def cancel_sync(session: Session, actor_id: uuid.UUID, job_id: uuid.UUID) -> dict:
    job = session.get(AdSyncJob, job_id)
    if job is None:
        raise AdValidationError("Sync job was not found.")
    if job.status in {JOB_COMPLETED, JOB_FAILED, JOB_CANCELLED}:
        return job_dto(job)
    job.cancel_requested = True
    _audit(session, action="sync_cancel", actor_id=actor_id, connection_id=job.connection_id, detail=f"job={job.id}")
    session.flush()
    return job_dto(job)


def run_sync_job(session: Session, settings: Settings, gateway: LdapGateway, job_id: uuid.UUID) -> None:
    job = session.get(AdSyncJob, job_id)
    if job is None:
        return
    row = session.get(AdConnection, job.connection_id)
    if row is None:
        job.status = JOB_FAILED
        job.safe_error = "Connection missing."
        job.completed_at = _now()
        return
    if job.cancel_requested:
        job.status = JOB_CANCELLED
        job.completed_at = _now()
        return
    job.status = JOB_RUNNING
    job.started_at = _now()
    session.flush()
    try:
        params = _bind_params(session, settings, row)
        ou_dns = [
            s.distinguished_name
            for s in session.execute(
                select(AdOuScope).where(AdOuScope.connection_id == row.id, AdOuScope.selected.is_(True))
            ).scalars().all()
        ]
        users = gateway.search_users(params, ou_dns=ou_dns, limit=2000)
        if job.cancel_requested:
            job.status = JOB_CANCELLED
            job.completed_at = _now()
            session.flush()
            return
        groups = gateway.search_groups(params, ou_dns=ou_dns, limit=1000)
        job.enumeration_complete = True
        job.users_discovered = len(users)
        job.groups_discovered = len(groups)
        email_index = _google_email_index(session)
        seen_guids: set[str] = set()
        now = _now()
        for record in users:
            if job.cancel_requested:
                job.status = JOB_CANCELLED
                job.completed_at = now
                session.flush()
                return
            if not record.object_guid:
                job.failed_count += 1
                continue
            seen_guids.add(record.object_guid)
            digest = _content_hash(record)
            existing = session.execute(
                select(AdDirectoryUser).where(
                    AdDirectoryUser.connection_id == row.id,
                    AdDirectoryUser.object_guid == record.object_guid,
                )
            ).scalar_one_or_none()
            candidates = _match_candidates(record, email_index)
            if existing is None:
                mapping = MAP_UNLINKED
                cand_user = None
                cand_email = ""
                if len(candidates) == 1:
                    mapping = MAP_PENDING
                    cand_user = candidates[0][0]
                    cand_email = candidates[0][1]
                elif len(candidates) > 1:
                    mapping = MAP_CONFLICT
                existing = AdDirectoryUser(
                    id=uuid.uuid4(),
                    connection_id=row.id,
                    object_guid=record.object_guid,
                    object_sid=record.object_sid,
                    sam_account_name=record.sam_account_name,
                    user_principal_name=record.user_principal_name,
                    display_name=record.display_name,
                    mail=record.mail,
                    department=record.department,
                    distinguished_name=record.distinguished_name,
                    account_enabled=record.account_enabled,
                    is_present=True,
                    qka_eligible=False,  # Discovery never enrolls; admin marks eligible later.
                    qka_archived=False,
                    department_override="",
                    mapping_status=mapping,
                    candidate_qka_user_id=cand_user,
                    candidate_google_email=cand_email,
                    content_hash=digest,
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(existing)
                job.users_added += 1
            else:
                if existing.content_hash == digest and existing.is_present:
                    job.users_unchanged += 1
                else:
                    existing.object_sid = record.object_sid
                    existing.sam_account_name = record.sam_account_name
                    existing.user_principal_name = record.user_principal_name
                    existing.display_name = record.display_name
                    existing.mail = record.mail
                    existing.department = record.department
                    existing.distinguished_name = record.distinguished_name
                    existing.account_enabled = record.account_enabled
                    existing.content_hash = digest
                    job.users_updated += 1
                existing.is_present = True
                existing.last_seen_at = now
                existing.updated_at = now
                # Refresh pending candidates only when unlinked/pending/conflict (never break linked).
                if existing.mapping_status in {MAP_UNLINKED, MAP_PENDING, MAP_CONFLICT}:
                    if len(candidates) == 1:
                        existing.mapping_status = MAP_PENDING
                        existing.candidate_qka_user_id = candidates[0][0]
                        existing.candidate_google_email = candidates[0][1]
                    elif len(candidates) > 1:
                        existing.mapping_status = MAP_CONFLICT
                        existing.candidate_qka_user_id = None
                        existing.candidate_google_email = ""
                    elif existing.mapping_status != MAP_LINKED:
                        existing.mapping_status = MAP_UNLINKED
                        existing.candidate_qka_user_id = None
                        existing.candidate_google_email = ""
            job.users_processed += 1

        # Incomplete sync protection: only mark missing when enumeration completed fully.
        if job.enumeration_complete and not job.cancel_requested:
            present_rows = session.execute(
                select(AdDirectoryUser).where(AdDirectoryUser.connection_id == row.id)
            ).scalars().all()
            for item in present_rows:
                if item.object_guid not in seen_guids and item.is_present:
                    # Soft-mark missing; do not delete ExternalIdentity or grants.
                    item.is_present = False
                    item.updated_at = now

        # Groups
        for g in groups:
            if not g.object_guid:
                continue
            grow = session.execute(
                select(AdDirectoryGroup).where(
                    AdDirectoryGroup.connection_id == row.id,
                    AdDirectoryGroup.object_guid == g.object_guid,
                )
            ).scalar_one_or_none()
            if grow is None:
                grow = AdDirectoryGroup(
                    id=uuid.uuid4(),
                    connection_id=row.id,
                    object_guid=g.object_guid,
                    object_sid=g.object_sid,
                    name=g.name,
                    distinguished_name=g.distinguished_name,
                    is_security_group=g.is_security_group,
                    is_present=True,
                    member_count=len(g.member_guids),
                    created_at=now,
                    updated_at=now,
                )
                session.add(grow)
            else:
                grow.object_sid = g.object_sid
                grow.name = g.name
                grow.distinguished_name = g.distinguished_name
                grow.is_security_group = g.is_security_group
                grow.is_present = True
                grow.member_count = len(g.member_guids)
                grow.updated_at = now
            for edge in session.execute(
                select(AdGroupMemberEdge).where(
                    AdGroupMemberEdge.connection_id == row.id,
                    AdGroupMemberEdge.group_object_guid == g.object_guid,
                )
            ).scalars().all():
                session.delete(edge)
            for member_dn in g.member_guids[:500]:
                session.add(
                    AdGroupMemberEdge(
                        id=uuid.uuid4(),
                        connection_id=row.id,
                        group_object_guid=g.object_guid,
                        member_object_guid=member_dn[:64],
                        member_object_sid=member_dn[:256],
                        member_kind="dn",
                        created_at=now,
                    )
                )

        job.linked_count = session.scalar(
            select(func.count()).select_from(AdDirectoryUser).where(
                AdDirectoryUser.connection_id == row.id, AdDirectoryUser.mapping_status == MAP_LINKED
            )
        ) or 0
        job.pending_count = session.scalar(
            select(func.count()).select_from(AdDirectoryUser).where(
                AdDirectoryUser.connection_id == row.id, AdDirectoryUser.mapping_status == MAP_PENDING
            )
        ) or 0
        job.conflict_count = session.scalar(
            select(func.count()).select_from(AdDirectoryUser).where(
                AdDirectoryUser.connection_id == row.id, AdDirectoryUser.mapping_status == MAP_CONFLICT
            )
        ) or 0
        job.status = JOB_COMPLETED
        job.completed_at = _now()
        row.last_successful_sync_at = job.completed_at
        row.last_sync_result = "completed"
        row.current_sync_job_id = None
        session.flush()
    except Exception as exc:
        job.status = JOB_FAILED
        job.safe_error = "Directory synchronization failed."
        job.completed_at = _now()
        row.last_sync_result = "failed"
        row.last_safe_error = job.safe_error
        row.current_sync_job_id = None
        _audit(
            session,
            action="sync_failed",
            actor_id=job.actor_user_id,
            connection_id=row.id,
            detail=type(exc).__name__,
        )
        session.flush()


def _mapping_label(status: int) -> str:
    return {0: "unlinked", 1: "pending", 2: "linked", 3: "conflict"}.get(status, "unlinked")


def _is_effectively_archived(row: AdDirectoryUser) -> bool:
    """Disabled/missing AD accounts are archived by default; manual flag also archives."""
    return bool(row.qka_archived) or (not row.is_present) or (not row.account_enabled)


def _archived_sql_clause():
    return or_(
        AdDirectoryUser.qka_archived.is_(True),
        AdDirectoryUser.is_present.is_(False),
        AdDirectoryUser.account_enabled.is_(False),
    )


def _directory_user_dto(session: Session, row: AdDirectoryUser) -> dict:
    suggested_email = row.candidate_google_email or None
    suggested_user_id = str(row.candidate_qka_user_id) if row.candidate_qka_user_id else None
    linked_google_email = None
    if row.linked_qka_user_id:
        identity = session.execute(
            select(ExternalIdentity)
            .options(selectinload(ExternalIdentity.google))
            .where(
                ExternalIdentity.qka_user_id == row.linked_qka_user_id,
                ExternalIdentity.provider == PROVIDER_GOOGLE,
                ExternalIdentity.link_status == LINKED,
            )
        ).scalars().first()
        if identity:
            linked_google_email = (
                (identity.google.primary_email if identity.google else None) or identity.primary_email or None
            )
    archived = _is_effectively_archived(row)
    can_mark_eligible = bool(row.is_present and row.account_enabled)
    # Enabled + eligible users cannot be manually archived.
    can_archive = bool(not (row.is_present and row.account_enabled and row.qka_eligible))
    can_unarchive = bool(row.qka_archived and row.is_present and row.account_enabled)
    can_link = bool(
        row.is_present
        and row.account_enabled
        and row.mapping_status in {MAP_UNLINKED, MAP_PENDING, MAP_CONFLICT}
        and (row.candidate_qka_user_id or row.mapping_status == MAP_UNLINKED)
    )
    return {
        "id": str(row.id),
        "objectGuid": row.object_guid,
        "objectSid": row.object_sid,
        "samAccountName": row.sam_account_name,
        "userPrincipalName": row.user_principal_name,
        "displayName": row.display_name,
        "mail": row.mail or None,
        "department": row.department or "",
        "departmentOverride": row.department_override or "",
        "effectiveDepartment": (row.department_override or row.department or "").strip(),
        "distinguishedName": row.distinguished_name,
        "accountEnabled": row.account_enabled,
        "isPresent": row.is_present,
        "accountStatus": (
            "missing" if not row.is_present else ("enabled" if row.account_enabled else "disabled")
        ),
        "qkaEligible": bool(row.qka_eligible),
        "eligibilityLabel": "eligible" if row.qka_eligible else "not_eligible",
        "qkaArchived": bool(row.qka_archived),
        "archived": archived,
        "archiveReason": (
            "missing"
            if not row.is_present
            else ("disabled" if not row.account_enabled else ("manual" if row.qka_archived else None))
        ),
        "mappingStatus": row.mapping_status,
        "mappingLabel": _mapping_label(row.mapping_status),
        "suggestedMatchEmail": suggested_email,
        "suggestedMatchUserId": suggested_user_id,
        "linkedGoogleEmail": linked_google_email,
        "linkedQkaUserId": str(row.linked_qka_user_id) if row.linked_qka_user_id else None,
        "candidateQkaUserId": suggested_user_id,
        "canMarkEligible": can_mark_eligible,
        "canArchive": can_archive and not archived,
        "canUnarchive": can_unarchive,
        "canApproveLink": can_link and row.mapping_status in {MAP_PENDING, MAP_CONFLICT},
        "canUnlink": row.mapping_status == MAP_LINKED,
    }


_USER_SORT_COLUMNS = {
    "displayName": AdDirectoryUser.display_name,
    "samAccountName": AdDirectoryUser.sam_account_name,
    "department": AdDirectoryUser.department,
    "qkaEligible": AdDirectoryUser.qka_eligible,
    "mappingLabel": AdDirectoryUser.mapping_status,
    "mappingStatus": AdDirectoryUser.mapping_status,
}


def _user_sort_exprs(sort_by: str, descending: bool):
    key = (sort_by or "").strip()
    primary = _USER_SORT_COLUMNS.get(key)
    if key == "accountStatus":
        # missing < disabled < enabled (asc)
        primary = case(
            (AdDirectoryUser.is_present.is_(False), 0),
            (AdDirectoryUser.account_enabled.is_(False), 1),
            else_=2,
        )
    if primary is None:
        primary = AdDirectoryUser.display_name
    expr = primary.desc() if descending else primary.asc()
    tie = AdDirectoryUser.sam_account_name.desc() if descending else AdDirectoryUser.sam_account_name.asc()
    return expr, tie


def list_users(
    session: Session,
    connection_id: uuid.UUID,
    *,
    search: str = "",
    mapping_status: int | None = None,
    account_status: str | None = None,
    eligibility: str | None = None,
    department: str | None = None,
    archived: bool = False,
    sort_by: str = "displayName",
    sort_desc: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    get_connection(session, connection_id)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    filters = [AdDirectoryUser.connection_id == connection_id]
    # Default list = active (present + enabled + not manually archived).
    # Archived list = disabled/missing OR manually archived.
    if archived:
        filters.append(_archived_sql_clause())
    else:
        filters.append(AdDirectoryUser.qka_archived.is_(False))
        filters.append(AdDirectoryUser.is_present.is_(True))
        filters.append(AdDirectoryUser.account_enabled.is_(True))
    q = (search or "").strip()
    if q:
        like = f"%{q}%"
        # Match name/username/department, and email/UPN local-part only.
        # Avoid "%edu%" matching every "@qehc.edu.sa" address.
        local_upn = func.split_part(AdDirectoryUser.user_principal_name, "@", 1)
        local_mail = func.split_part(AdDirectoryUser.mail, "@", 1)
        clauses = [
            AdDirectoryUser.sam_account_name.ilike(like),
            AdDirectoryUser.display_name.ilike(like),
            AdDirectoryUser.department.ilike(like),
            local_upn.ilike(like),
            local_mail.ilike(like),
        ]
        if "@" in q:
            clauses.extend(
                [
                    AdDirectoryUser.user_principal_name.ilike(like),
                    AdDirectoryUser.mail.ilike(like),
                ]
            )
        filters.append(or_(*clauses))
    if mapping_status is not None:
        filters.append(AdDirectoryUser.mapping_status == mapping_status)
    acct = (account_status or "").strip().lower()
    if acct == "enabled":
        filters.append(AdDirectoryUser.is_present.is_(True))
        filters.append(AdDirectoryUser.account_enabled.is_(True))
    elif acct == "disabled":
        filters.append(AdDirectoryUser.is_present.is_(True))
        filters.append(AdDirectoryUser.account_enabled.is_(False))
    elif acct == "missing":
        filters.append(AdDirectoryUser.is_present.is_(False))
    elig = (eligibility or "").strip().lower()
    if elig in {"eligible", "true", "1", "yes"}:
        filters.append(AdDirectoryUser.qka_eligible.is_(True))
    elif elig in {"not_eligible", "false", "0", "no"}:
        filters.append(AdDirectoryUser.qka_eligible.is_(False))
    dept = (department or "").strip()
    if dept:
        # Dropdown supplies the catalog value; match case-insensitively (exact label).
        filters.append(func.lower(AdDirectoryUser.department) == dept.lower())
    base = select(AdDirectoryUser).where(*filters)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    order_primary, order_tie = _user_sort_exprs(sort_by, sort_desc)
    rows = session.execute(
        base.order_by(order_primary, order_tie)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    return {
        "items": [_directory_user_dto(session, row) for row in rows],
        "total": int(total),
        "page": page,
        "pageSize": page_size,
        "sortBy": sort_by or "displayName",
        "sortDesc": bool(sort_desc),
        "archived": bool(archived),
    }


def list_departments(session: Session, connection_id: uuid.UUID) -> dict:
    """Distinct AD department values for the Users filter dropdown."""
    get_connection(session, connection_id)
    rows = session.execute(
        select(AdDirectoryUser.department)
        .where(
            AdDirectoryUser.connection_id == connection_id,
            AdDirectoryUser.department.is_not(None),
            AdDirectoryUser.department != "",
        )
        .distinct()
        .order_by(AdDirectoryUser.department)
    ).all()
    names = sorted({(value or "").strip() for (value,) in rows if (value or "").strip()}, key=str.lower)
    return {
        "items": [{"value": name, "label": name} for name in names],
        "total": len(names),
    }


def set_eligibility(
    session: Session,
    actor_id: uuid.UUID,
    connection_id: uuid.UUID,
    directory_user_ids: list[uuid.UUID],
    *,
    eligible: bool,
) -> dict:
    """Mark discovered AD users eligible for Access Management enrollment. Never writes to AD."""
    get_connection(session, connection_id)
    ids = list(dict.fromkeys(directory_user_ids))[:500]
    if not ids:
        raise AdValidationError("Select at least one directory user.")
    updated = 0
    skipped: list[dict] = []
    errors: list[dict] = []
    for uid in ids:
        row = session.get(AdDirectoryUser, uid)
        if row is None or row.connection_id != connection_id:
            errors.append({"id": str(uid), "error": "Directory user was not found."})
            continue
        if eligible and (not row.is_present or not row.account_enabled):
            skipped.append(
                {
                    "id": str(uid),
                    "samAccountName": row.sam_account_name,
                    "reason": "Disabled or missing AD accounts cannot be newly marked eligible.",
                }
            )
            continue
        if bool(row.qka_eligible) == bool(eligible):
            continue
        # Unchecking eligibility must NOT revoke existing QKA accounts — only clears the flag.
        row.qka_eligible = bool(eligible)
        if eligible and row.qka_archived:
            # Eligible enabled users cannot remain manually archived.
            row.qka_archived = False
        row.updated_at = _now()
        updated += 1
        _audit(
            session,
            action="eligibility_enable" if eligible else "eligibility_disable",
            actor_id=actor_id,
            connection_id=connection_id,
            detail=f"guid={row.object_guid}; sam={row.sam_account_name}; eligible={eligible}",
        )
    session.flush()
    return {
        "ok": len(errors) == 0,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "eligible": bool(eligible),
    }


def set_archived(
    session: Session,
    actor_id: uuid.UUID,
    connection_id: uuid.UUID,
    directory_user_ids: list[uuid.UUID],
    *,
    archived: bool,
) -> dict:
    """Manually archive/unarchive directory users in QKA. Never writes to AD.

    Disabled/missing accounts are already treated as archived (no flag required).
    Enabled + eligible users cannot be archived.
    """
    get_connection(session, connection_id)
    ids = list(dict.fromkeys(directory_user_ids))[:500]
    if not ids:
        raise AdValidationError("Select at least one directory user.")
    updated = 0
    skipped: list[dict] = []
    errors: list[dict] = []
    for uid in ids:
        row = session.get(AdDirectoryUser, uid)
        if row is None or row.connection_id != connection_id:
            errors.append({"id": str(uid), "error": "Directory user was not found."})
            continue
        if archived:
            if row.is_present and row.account_enabled and row.qka_eligible:
                skipped.append(
                    {
                        "id": str(uid),
                        "samAccountName": row.sam_account_name,
                        "reason": "Enabled eligible users cannot be archived.",
                    }
                )
                continue
            if not row.is_present or not row.account_enabled:
                # Already archived by account status — nothing to store.
                skipped.append(
                    {
                        "id": str(uid),
                        "samAccountName": row.sam_account_name,
                        "reason": "Disabled or missing accounts are already archived.",
                    }
                )
                continue
            if row.qka_archived:
                continue
            row.qka_archived = True
        else:
            if not row.qka_archived:
                skipped.append(
                    {
                        "id": str(uid),
                        "samAccountName": row.sam_account_name,
                        "reason": "User is not manually archived (disabled/missing stay archived).",
                    }
                )
                continue
            if not row.is_present or not row.account_enabled:
                skipped.append(
                    {
                        "id": str(uid),
                        "samAccountName": row.sam_account_name,
                        "reason": "Disabled or missing accounts cannot be restored from archive.",
                    }
                )
                continue
            row.qka_archived = False
        row.updated_at = _now()
        updated += 1
        _audit(
            session,
            action="archive_enable" if archived else "archive_restore",
            actor_id=actor_id,
            connection_id=connection_id,
            detail=f"guid={row.object_guid}; sam={row.sam_account_name}; archived={archived}",
        )
    session.flush()
    return {
        "ok": len(errors) == 0,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "archived": bool(archived),
    }


def search_eligible_directory_users(
    session: Session,
    *,
    search: str = "",
    connection_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    """Access Management AD picker: eligible + present + enabled only."""
    page = max(1, page)
    page_size = max(1, min(page_size, 50))
    filters = [
        AdDirectoryUser.qka_eligible.is_(True),
        AdDirectoryUser.is_present.is_(True),
        AdDirectoryUser.account_enabled.is_(True),
        AdDirectoryUser.qka_archived.is_(False),
    ]
    if connection_id is not None:
        filters.append(AdDirectoryUser.connection_id == connection_id)
    q = (search or "").strip()
    if q:
        like = f"%{q}%"
        filters.append(
            or_(
                AdDirectoryUser.display_name.ilike(like),
                AdDirectoryUser.sam_account_name.ilike(like),
                AdDirectoryUser.department.ilike(like),
                AdDirectoryUser.mail.ilike(like),
            )
        )
    base = select(AdDirectoryUser).where(*filters)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.execute(
        base.order_by(AdDirectoryUser.display_name, AdDirectoryUser.sam_account_name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    items = []
    for row in rows:
        already = row.mapping_status == MAP_LINKED and row.linked_qka_user_id is not None
        items.append(
            {
                "id": str(row.id),
                "objectGuid": row.object_guid,
                "objectSid": row.object_sid,
                "displayName": row.display_name,
                "samAccountName": row.sam_account_name,
                "department": row.department or "",
                "departmentBlank": not bool((row.department or "").strip()),
                "mail": row.mail or None,
                "alreadyLinked": already,
                "linkedQkaUserId": str(row.linked_qka_user_id) if row.linked_qka_user_id else None,
                "connectionId": str(row.connection_id),
            }
        )
    return {"items": items, "total": int(total), "page": page, "pageSize": page_size}


def list_groups(session: Session, connection_id: uuid.UUID, *, search: str = "", page: int = 1, page_size: int = 50) -> dict:
    get_connection(session, connection_id)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    filters = [AdDirectoryGroup.connection_id == connection_id, AdDirectoryGroup.is_present.is_(True)]
    q = (search or "").strip()
    if q:
        like = f"%{q}%"
        filters.append(or_(AdDirectoryGroup.name.ilike(like), AdDirectoryGroup.distinguished_name.ilike(like)))
    base = select(AdDirectoryGroup).where(*filters)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.execute(
        base.order_by(AdDirectoryGroup.name).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "objectGuid": r.object_guid,
                "objectSid": r.object_sid,
                "name": r.name,
                "distinguishedName": r.distinguished_name,
                "isSecurityGroup": r.is_security_group,
                "memberCount": r.member_count,
            }
            for r in rows
        ],
        "total": int(total),
        "page": page,
        "pageSize": page_size,
    }


def resolve_nested_members(
    session: Session, connection_id: uuid.UUID, group_object_guid: str, *, max_depth: int = 5
) -> dict:
    """Bounded nested group resolution from stored edges (DN-based)."""
    visited: set[str] = set()
    users: list[str] = []

    def walk(guid: str, depth: int) -> None:
        if depth > max_depth or guid in visited:
            return
        visited.add(guid)
        edges = session.execute(
            select(AdGroupMemberEdge).where(
                AdGroupMemberEdge.connection_id == connection_id,
                AdGroupMemberEdge.group_object_guid == guid,
            )
        ).scalars().all()
        for edge in edges:
            dn = edge.member_object_sid or edge.member_object_guid
            if not dn:
                continue
            # If DN looks like a group, try recurse by matching group DN
            group = session.execute(
                select(AdDirectoryGroup).where(
                    AdDirectoryGroup.connection_id == connection_id,
                    AdDirectoryGroup.distinguished_name == dn,
                )
            ).scalar_one_or_none()
            if group is not None:
                walk(group.object_guid, depth + 1)
            else:
                users.append(dn)

    walk(group_object_guid, 0)
    return {"groupObjectGuid": group_object_guid, "memberDns": users[:1000], "visitedGroups": len(visited)}


def approve_link(
    session: Session,
    actor_id: uuid.UUID,
    connection_id: uuid.UUID,
    directory_user_id: uuid.UUID,
    *,
    qka_user_id: uuid.UUID | None = None,
) -> dict:
    """Admin-approved link. Creates/updates AD ExternalIdentity only; never touches Google rows."""
    get_connection(session, connection_id)
    row = session.get(AdDirectoryUser, directory_user_id)
    if row is None or row.connection_id != connection_id:
        raise AdValidationError("Directory user was not found.")
    if not row.is_present or not row.account_enabled:
        raise AdValidationError("Disabled or missing AD accounts cannot be linked.")
    target = qka_user_id or row.candidate_qka_user_id
    if target is None:
        raise AdValidationError("A QKA user target is required to approve the link.")
    user = session.get(QkaUser, target)
    if user is None:
        raise AdValidationError("QKA user was not found.")
    if not row.object_sid:
        raise AdValidationError("AD objectSid is required before linking.")
    # Prevent one AD identity linking to two users
    other = session.execute(
        select(AdDirectoryUser).where(
            AdDirectoryUser.connection_id == connection_id,
            AdDirectoryUser.object_guid == row.object_guid,
            AdDirectoryUser.mapping_status == MAP_LINKED,
            AdDirectoryUser.id != row.id,
        )
    ).scalar_one_or_none()
    if other is not None:
        raise AdConflictError("This AD identity is already linked.")
    # Prevent same SID linked via ExternalIdentity to a different user
    existing_ad = session.execute(
        select(ExternalIdentity).where(
            ExternalIdentity.provider == PROVIDER_AD,
            ExternalIdentity.stable_external_id == row.object_sid,
        )
    ).scalar_one_or_none()
    now = _now()
    if existing_ad is not None:
        if existing_ad.qka_user_id and existing_ad.qka_user_id != target:
            raise AdConflictError("This AD SID is already linked to another QKA user.")
        existing_ad.qka_user_id = target
        existing_ad.domain_username = row.sam_account_name
        existing_ad.upn = row.user_principal_name or None
        existing_ad.primary_email = row.mail or None
        existing_ad.account_status = ACCOUNT_ENABLED if row.account_enabled else 1
        existing_ad.link_status = LINKED
        existing_ad.directory_presence = 1 if row.is_present else 0
        existing_ad.updated_at = now
        identity = existing_ad
    else:
        identity = ExternalIdentity(
            id=uuid.uuid4(),
            qka_user_id=target,
            provider=PROVIDER_AD,
            stable_external_id=row.object_sid,
            domain_username=row.sam_account_name,
            upn=row.user_principal_name or None,
            primary_email=row.mail or None,
            account_status=ACCOUNT_ENABLED if row.account_enabled else 1,
            link_status=LINKED,
            directory_presence=1 if row.is_present else 0,
            created_at=now,
            updated_at=now,
        )
        session.add(identity)
    row.mapping_status = MAP_LINKED
    row.linked_qka_user_id = target
    row.linked_external_identity_id = identity.id
    row.updated_at = now
    _audit(
        session,
        action="identity_link",
        actor_id=actor_id,
        connection_id=connection_id,
        detail=f"guid={row.object_guid}; user={target}",
    )
    session.flush()
    return list_users(session, connection_id, search=row.sam_account_name, page_size=1)["items"][0]


def unlink_identity(
    session: Session,
    actor_id: uuid.UUID,
    connection_id: uuid.UUID,
    directory_user_id: uuid.UUID,
) -> dict:
    get_connection(session, connection_id)
    row = session.get(AdDirectoryUser, directory_user_id)
    if row is None or row.connection_id != connection_id:
        raise AdValidationError("Directory user was not found.")
    if row.linked_external_identity_id:
        identity = session.get(ExternalIdentity, row.linked_external_identity_id)
        if identity is not None and identity.provider == PROVIDER_AD:
            # Unlink AD identity only — do not delete Google identities or the QkaUser.
            identity.link_status = 0
            identity.qka_user_id = None
            identity.updated_at = _now()
    row.mapping_status = MAP_UNLINKED
    row.linked_qka_user_id = None
    row.linked_external_identity_id = None
    row.updated_at = _now()
    _audit(
        session,
        action="identity_unlink",
        actor_id=actor_id,
        connection_id=connection_id,
        detail=f"guid={row.object_guid}",
    )
    session.flush()
    return {"ok": True}


def sync_history(session: Session, connection_id: uuid.UUID, *, take: int = 50) -> list[dict]:
    get_connection(session, connection_id)
    rows = session.execute(
        select(AdSyncJob)
        .where(AdSyncJob.connection_id == connection_id)
        .order_by(AdSyncJob.created_at.desc())
        .limit(max(1, min(take, 100)))
    ).scalars().all()
    return [job_dto(r) for r in rows]


def audit_history(session: Session, connection_id: uuid.UUID | None = None, *, take: int = 50) -> list[dict]:
    q = select(AdAuditEvent).order_by(AdAuditEvent.created_at.desc()).limit(max(1, min(take, 100)))
    if connection_id:
        q = q.where(AdAuditEvent.connection_id == connection_id)
    rows = session.execute(q).scalars().all()
    return [
        {
            "id": str(r.id),
            "connectionId": str(r.connection_id) if r.connection_id else None,
            "action": r.action,
            "actorUserId": str(r.actor_user_id) if r.actor_user_id else None,
            "detail": r.detail,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def _resolve_enrollment_department(
    session: Session,
    row: AdDirectoryUser,
    department_override: str | None,
) -> str:
    from qka_api.services import user_directory as directory

    override = (department_override if department_override is not None else row.department_override or "").strip()
    ad_dept = (row.department or "").strip()
    options = [i["value"] for i in directory.list_department_options(session)["items"]]
    if override:
        matched = directory.match_department_option(override, options)
        if matched is None:
            raise AdValidationError("Select a QKA department from the list.")
        return matched[:512]
    matched_ad = directory.match_department_option(ad_dept, options)
    if matched_ad:
        return matched_ad[:512]
    raise AdValidationError(
        "AD department is not set or does not match a QKA department. "
        "Choose a QKA department before creating the user."
    )


def _link_ad_identity_to_user(
    session: Session,
    *,
    row: AdDirectoryUser,
    user: QkaUser,
    actor_id: uuid.UUID,
) -> ExternalIdentity:
    if not row.object_sid:
        raise AdValidationError("AD objectSid is required before linking.")
    existing_ad = session.execute(
        select(ExternalIdentity).where(
            ExternalIdentity.provider == PROVIDER_AD,
            ExternalIdentity.stable_external_id == row.object_sid,
        )
    ).scalar_one_or_none()
    now = _now()
    if existing_ad is not None:
        if existing_ad.qka_user_id and existing_ad.qka_user_id != user.id and existing_ad.link_status == LINKED:
            raise AdConflictError("This AD SID is already linked to another QKA user.")
        existing_ad.qka_user_id = user.id
        existing_ad.domain_username = row.sam_account_name
        existing_ad.upn = row.user_principal_name or None
        existing_ad.primary_email = row.mail or None
        existing_ad.account_status = ACCOUNT_ENABLED
        existing_ad.link_status = LINKED
        existing_ad.directory_presence = 1
        existing_ad.updated_at = now
        identity = existing_ad
    else:
        identity = ExternalIdentity(
            id=uuid.uuid4(),
            qka_user_id=user.id,
            provider=PROVIDER_AD,
            stable_external_id=row.object_sid,
            domain_username=row.sam_account_name,
            upn=row.user_principal_name or None,
            primary_email=row.mail or None,
            account_status=ACCOUNT_ENABLED,
            link_status=LINKED,
            directory_presence=1,
            created_at=now,
            updated_at=now,
        )
        session.add(identity)
        session.flush()
    row.mapping_status = MAP_LINKED
    row.linked_qka_user_id = user.id
    row.linked_external_identity_id = identity.id
    row.updated_at = now
    _audit(
        session,
        action="identity_link",
        actor_id=actor_id,
        connection_id=row.connection_id,
        detail=f"guid={row.object_guid}; user={user.id}",
    )
    return identity


def enroll_directory_user(
    session: Session,
    actor_id: uuid.UUID,
    *,
    object_guid: str,
    department_override: str | None = None,
    contact_email: str | None = None,
    link_existing_qka_user_id: uuid.UUID | None = None,
) -> dict:
    """Create or link a QKA application user from an eligible AD directory identity.

    Does not grant Synology/Source Access, Local Chat, or MCP. Does not implement AD login.
    """
    guid = (object_guid or "").strip()
    if not guid:
        raise AdValidationError("Select an Active Directory user by objectGUID.")
    # Prefer a present inventory row; objectGUID is unique per connection.
    rows = session.execute(
        select(AdDirectoryUser)
        .where(AdDirectoryUser.object_guid == guid)
        .order_by(AdDirectoryUser.is_present.desc(), AdDirectoryUser.updated_at.desc())
    ).scalars().all()
    row = next((r for r in rows if r.is_present and r.account_enabled), None) or (rows[0] if rows else None)
    if row is None:
        raise AdValidationError("Active Directory user was not found in the synchronized inventory.")
    if not row.qka_eligible:
        raise AdValidationError("This AD user is not marked eligible for QKA enrollment.")
    if not row.is_present or not row.account_enabled:
        raise AdValidationError("Disabled or missing AD accounts cannot be enrolled.")
    if not row.object_sid:
        raise AdValidationError("AD objectSid is required before enrollment.")

    # Concurrent / duplicate SID protection
    existing_ad = session.execute(
        select(ExternalIdentity).where(
            ExternalIdentity.provider == PROVIDER_AD,
            ExternalIdentity.stable_external_id == row.object_sid,
            ExternalIdentity.link_status == LINKED,
            ExternalIdentity.qka_user_id.is_not(None),
        )
    ).scalar_one_or_none()
    if existing_ad is not None and link_existing_qka_user_id is None:
        if row.linked_qka_user_id and row.linked_qka_user_id == existing_ad.qka_user_id:
            raise AdConflictError(
                f"This AD identity is already linked to QKA user {existing_ad.qka_user_id}."
            )
        raise AdConflictError("This AD SID is already linked to a QKA user.")

    contact = None
    raw_email = (contact_email if contact_email is not None else row.mail or "").strip()
    if raw_email:
        contact, err = try_normalize(raw_email)
        if contact is None:
            raise AdValidationError(err or "Contact email is invalid.")

    department = _resolve_enrollment_department(session, row, department_override)
    now = _now()

    # Optional: explicit link to existing Google-backed QkaUser (never auto-merge).
    if link_existing_qka_user_id is not None:
        user = session.get(QkaUser, link_existing_qka_user_id)
        if user is None:
            raise AdValidationError("Existing QKA user was not found.")
        # Do not overwrite Google identity — only attach AD.
        _link_ad_identity_to_user(session, row=row, user=user, actor_id=actor_id)
        if department and not (user.directory_department or "").strip():
            user.directory_department = department
        if contact and not user.contact_email:
            user.contact_email = contact
        user.updated_at = now
        row.department_override = department if department != (row.department or "").strip() else row.department_override
        get_or_create_chat_access_for_user(session, user.id)
        _audit(
            session,
            action="ad_enroll_link_existing",
            actor_id=actor_id,
            connection_id=row.connection_id,
            detail=f"guid={row.object_guid}; user={user.id}",
        )
        session.flush()
        return {
            "created": False,
            "linkedExisting": True,
            "qkaUserId": str(user.id),
            "displayName": user.display_name,
            "domainUsername": row.sam_account_name,
            "department": user.directory_department or department,
            "contactEmail": user.contact_email,
            "objectGuid": row.object_guid,
            "objectSid": row.object_sid,
            "loginEnabled": bool(user.login_enabled),
            "adOnlyLoginImplemented": False,
        }

    if row.mapping_status == MAP_LINKED and row.linked_qka_user_id:
        raise AdConflictError(f"This AD identity is already linked to QKA user {row.linked_qka_user_id}.")

    # Matching Google user suggestion — require explicit linkExistingQkaUserId instead of auto-merge.
    if contact:
        google_hit = session.execute(
            select(ExternalIdentity)
            .options(selectinload(ExternalIdentity.user))
            .where(
                ExternalIdentity.provider == PROVIDER_GOOGLE,
                ExternalIdentity.link_status == LINKED,
                ExternalIdentity.qka_user_id.is_not(None),
                or_(
                    ExternalIdentity.primary_email == contact,
                    ExternalIdentity.upn == contact,
                ),
            )
        ).scalars().first()
        if google_hit is None:
            # Also match GoogleWorkspaceAccount
            google_hit_row = session.execute(
                select(ExternalIdentity)
                .join(GoogleWorkspaceAccount, GoogleWorkspaceAccount.external_identity_id == ExternalIdentity.id)
                .where(
                    ExternalIdentity.provider == PROVIDER_GOOGLE,
                    ExternalIdentity.link_status == LINKED,
                    ExternalIdentity.qka_user_id.is_not(None),
                    GoogleWorkspaceAccount.primary_email == contact,
                )
            ).scalars().first()
            google_hit = google_hit_row
        if google_hit is not None and google_hit.qka_user_id:
            raise AdConflictError(
                "A Google-linked QKA user already matches this email. "
                f"Confirm Link to Existing User (qkaUserId={google_hit.qka_user_id})."
            )

    # Create new QkaUser — AD-only login is NOT enabled (browser auth remains Google/local).
    user = QkaUser(
        id=uuid.uuid4(),
        display_name=(row.display_name or row.sam_account_name or "AD User")[:256],
        status=USER_ACTIVE,
        effective_department_id=None,
        directory_department=department,
        contact_email=contact,
        login_enabled=False,
        is_directory_operator=False,
        is_super_admin=False,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.flush()
    _link_ad_identity_to_user(session, row=row, user=user, actor_id=actor_id)
    if department != (row.department or "").strip():
        row.department_override = department
    # Contact email is informational only — never auto-create AllowedEmail / Google allowlist.
    # Google Workspace access requires a separate explicit allowlist + verified bind.
    get_or_create_chat_access_for_user(session, user.id)
    _audit(
        session,
        action="ad_enroll_create",
        actor_id=actor_id,
        connection_id=row.connection_id,
        detail=f"guid={row.object_guid}; user={user.id}; sam={row.sam_account_name}",
    )
    session.flush()
    return {
        "created": True,
        "linkedExisting": False,
        "qkaUserId": str(user.id),
        "displayName": user.display_name,
        "domainUsername": row.sam_account_name,
        "department": department,
        "contactEmail": contact,
        "objectGuid": row.object_guid,
        "objectSid": row.object_sid,
        "loginEnabled": False,
        "adOnlyLoginImplemented": False,
        "localChatEnabled": False,
        "mcpEnabled": False,
    }
