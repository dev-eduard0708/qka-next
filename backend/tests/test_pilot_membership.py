"""Pilot group membership management (mocked AD — no live domain writes)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from qka_api.services import gpo_engine, pilot_membership
from qka_shared.models import DEPLOY_SCHEDULED, AgentDeploymentJob
from tests.conftest import ALLOWED_USER, auth_cookies
from tests.test_agent_deployment import PILOT_SID, _sync_workstations
from tests.test_workstations import _admin, _install

PILOT = "QKA-Agent-Pilot-Computers"


def _prereq_ok():
    return {
        "deployEnabled": True,
        "httpsConfigured": True,
        "httpsEndpoint": "https://qka-agent.qehc.edu.sa",
        "tlsTrust": "trusted",
        "msiPresent": True,
        "msiPath": "x",
        "gpoEngineAvailable": True,
        "gpoReason": "ok",
        "gpoTargetGroup": PILOT,
        "machineIdentityEnrollmentReady": True,
        "manualEnrollmentCodeFallback": False,
        "blockers": [],
        "confirmationPhrase": "DEPLOY",
        "maxBulk": 50,
        "realPcDeployAllowed": True,
        "note": "",
    }


def test_super_admin_add_pilot_membership_mocked(client):
    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    target = next(i for i in items if i["computerName"] == "PILOT-PC")

    def fake_ps(*, operation, computer_sid, sam_account_name):
        assert operation == "add"
        assert computer_sid == PILOT_SID
        return {
            "ok": True,
            "result": "added",
            "detail": "Computer added to pilot group.",
            "memberAfter": True,
            "computerName": "PILOT-PC.qehc.edu.sa",
            "samAccountName": "PILOT-PC$",
            "distinguishedName": target["distinguishedName"],
            "objectSid": PILOT_SID,
            "groupName": PILOT,
        }

    with (
        patch.object(pilot_membership, "_run_membership_ps", side_effect=fake_ps),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})),
        patch("qka_api.services.agent_deployment.deployment_prerequisites", return_value=_prereq_ok()),
    ):
        res = client.post(
            "/api/v1/admin/workstations/deploy/pilot-membership",
            headers=headers,
            json={"workstationIds": [target["id"]], "operation": "add"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pilotGroup"] == PILOT
    assert body["allSucceeded"] is True
    assert body["results"][0]["result"] == "added"
    assert body["results"][0]["pilotGroupMember"] is True
    assert body["workstations"][0]["deployAgent"]["pilotGroupMember"] is True
    note = (body.get("note") or "").lower()
    assert "does not install" in note or "separate" in note


def test_remove_blocked_while_active_job(client):
    from datetime import datetime, timezone

    from qka_api.db import create_session_factory
    from qka_shared.config import get_settings
    from sqlalchemy import select
    from qka_shared.models import QkaUser

    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    target = next(i for i in items if i["computerName"] == "PILOT-PC")

    settings = get_settings()
    _engine, factory = create_session_factory(settings)
    now = datetime.now(timezone.utc)
    with factory() as session:
        actor = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        assert actor is not None
        session.add(
            AgentDeploymentJob(
                id=uuid.uuid4(),
                workstation_id=uuid.UUID(target["id"]),
                requested_by_user_id=actor.id,
                status=DEPLOY_SCHEDULED,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    with (
        patch.object(gpo_engine, "computer_sid_in_pilot_group", return_value=True),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})),
        patch("qka_api.services.agent_deployment.deployment_prerequisites", return_value=_prereq_ok()),
    ):
        res = client.post(
            "/api/v1/admin/workstations/deploy/pilot-membership",
            headers=headers,
            json={"workstationIds": [target["id"]], "operation": "remove"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["results"][0]["ok"] is False
    assert body["results"][0]["result"] == "active_deployment"


def test_non_admin_rejected(client):
    _install(client)
    res = client.post(
        "/api/v1/admin/workstations/deploy/pilot-membership",
        cookies=auth_cookies(ALLOWED_USER),
        json={"workstationIds": [str(uuid.uuid4())], "operation": "add"},
    )
    assert res.status_code == 403


def test_duplicate_add_idempotent(client):
    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    target = next(i for i in items if i["computerName"] == "PILOT-PC")

    def already(*, operation, computer_sid, sam_account_name):
        return {
            "ok": True,
            "result": "already_member",
            "detail": "already",
            "memberAfter": True,
            "objectSid": PILOT_SID,
            "groupName": PILOT,
        }

    with (
        patch.object(pilot_membership, "_run_membership_ps", side_effect=already),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})),
        patch("qka_api.services.agent_deployment.deployment_prerequisites", return_value=_prereq_ok()),
    ):
        res = client.post(
            "/api/v1/admin/workstations/deploy/pilot-membership",
            headers=headers,
            json={"workstationIds": [target["id"]], "operation": "add"},
        )
    assert res.status_code == 200
    assert res.json()["results"][0]["result"] == "already_member"
    assert res.json()["results"][0]["ok"] is True


def test_rejects_invalid_operation_and_extra_group(client):
    _install(client)
    headers = _admin(client)
    bad = client.post(
        "/api/v1/admin/workstations/deploy/pilot-membership",
        headers=headers,
        json={"workstationIds": [str(uuid.uuid4())], "operation": "rename-group"},
    )
    assert bad.status_code == 400
    # Extra group field must be ignored by schema (not applied).
    with patch.object(
        pilot_membership,
        "_run_membership_ps",
        return_value={"ok": True, "result": "added", "memberAfter": True, "detail": "x", "objectSid": PILOT_SID},
    ), patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})), patch(
        "qka_api.services.agent_deployment.deployment_prerequisites", return_value=_prereq_ok()
    ):
        items = _sync_workstations(client, headers)
        target = next(i for i in items if i["computerName"] == "PILOT-PC")
        res = client.post(
            "/api/v1/admin/workstations/deploy/pilot-membership",
            headers=headers,
            json={
                "workstationIds": [target["id"]],
                "operation": "add",
                "group": "Domain Admins",
                "groupDn": "CN=Evil,DC=qehc,DC=edu,DC=sa",
            },
        )
    assert res.status_code == 200
    assert res.json()["pilotGroup"] == PILOT
