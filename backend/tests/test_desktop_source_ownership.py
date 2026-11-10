"""Agent deploy ≠ employee ownership; desktop source access is ownership + login gated."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select

from qka_api.services import agent_deployment as deploy_svc
from qka_api.services import gpo_engine
from qka_shared.config import Settings
from qka_shared.models import (
    DEVICE_ACTIVE,
    INDEX_IDLE,
    DesktopDevice,
    ExternalIdentity,
    MachineEnrollmentToken,
    QkaUser,
)
from qka_shared.source_access import authorize_desktop_source, evaluate_desktop_source_access
from tests.conftest import ALLOWED_USER, DENIED_USER
from tests.test_agent_deployment import PILOT_SID, _admin, _install, _sync_workstations


LOGIN_DISABLED_OWNER = uuid.UUID("bbbbbbbb-1111-4111-8111-111111111111")
SID_LOGIN_DISABLED = "S-1-5-21-9999-9999-9999-4242"


def _seed_login_disabled_owner(session):
    now = datetime.now(timezone.utc)
    existing = session.get(QkaUser, LOGIN_DISABLED_OWNER)
    if existing is None:
        session.add(
            QkaUser(
                id=LOGIN_DISABLED_OWNER,
                display_name="Login Disabled Owner",
                status=0,
                login_enabled=False,
                is_directory_operator=False,
                is_super_admin=False,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            ExternalIdentity(
                id=uuid.uuid4(),
                qka_user_id=LOGIN_DISABLED_OWNER,
                provider=0,
                stable_external_id=SID_LOGIN_DISABLED,
                domain_username="login.disabled",
                primary_email="login.disabled@qehc.edu.sa",
                account_status=0,
                link_status=2,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def test_unassigned_device_can_approve_folders_and_index(client):
    headers = _admin(client)
    issued = client.post(
        "/api/v1/admin/desktop-devices/enrollment-codes",
        headers=headers,
        json={"label": "unassigned-index"},
    )
    assert issued.status_code == 200, issued.text
    code = issued.json()["enrollmentCode"]

    enrolled = client.post(
        "/api/v1/agent/desktop/enroll",
        json={
            "enrollmentCode": code,
            "computerName": "UNASSIGNED-INDEX-PC",
            "deviceFingerprint": "unassigned-index-fp-1",
            "agentVersion": "0.1.0",
        },
    )
    assert enrolled.status_code == 200, enrolled.text
    device_id = enrolled.json()["deviceId"]
    device_key = enrolled.json()["deviceKey"]
    auth = {"X-QKA-Device-Id": device_id, "X-QKA-Device-Key": device_key}

    folders = client.put(
        f"/api/v1/admin/desktop-devices/{device_id}/folders",
        headers=headers,
        json={"folders": [{"path": r"C:\QKA-Pilot-Docs", "includeSubfolders": True}]},
    )
    assert folders.status_code == 200, folders.text

    started = client.post(
        f"/api/v1/admin/desktop-devices/{device_id}/command",
        headers=headers,
        json={"command": "start"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["indexingState"] == "running"
    assert started.json()["employeeAssigned"] is False
    assert started.json()["sourceAccessRestricted"] is True

    policy = client.get("/api/v1/agent/desktop/policy", headers=auth)
    assert policy.status_code == 200
    assert policy.json()["indexingEnabled"] is True
    assert policy.json()["sourceAccessRestricted"] is True
    assert policy.json()["employeeAssigned"] is False


def test_machine_enroll_with_login_disabled_owner(client, tmp_path):
    _install(client)
    headers = _admin(client)
    with client.app.state.session_factory() as session:
        _seed_login_disabled_owner(session)

    items = _sync_workstations(client, headers)
    pilot = next(i for i in items if i["computerName"] == "PILOT-PC")
    assigned = client.put(
        f"/api/v1/admin/workstations/{pilot['id']}/employee",
        headers=headers,
        json={"qkaUserId": str(LOGIN_DISABLED_OWNER)},
    )
    assert assigned.status_code == 200, assigned.text

    msi = tmp_path / "QKA-Agent.msi"
    msi.write_bytes(b"MZ")
    ready = gpo_engine.GpoEngineStatus(
        available=True,
        reason="ok",
        target_group="QKA-Agent-Pilot-Computers",
        msi_path=str(msi),
        msi_present=True,
        gpmc_present=True,
    )
    settings = Settings(agent_public_base_url="https://agent.qehc.internal")
    with (
        patch.object(gpo_engine, "probe_gpo_engine", return_value=ready),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})),
        patch.object(deploy_svc, "deployment_prerequisites") as prereq_fn,
    ):
        prereq_fn.return_value = {
            **deploy_svc.deployment_prerequisites(settings),
            "deployEnabled": True,
            "httpsConfigured": True,
            "httpsEndpoint": "https://agent.qehc.internal",
            "blockers": [],
            "gpoEngineAvailable": True,
            "msiPresent": True,
            "maxBulk": 50,
            "gpoTargetGroup": "QKA-Agent-Pilot-Computers",
        }
        created = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [pilot["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
        assert created.status_code == 200, created.text
        token = created.json()["jobs"][0]["machineEnrollmentToken"]
        sid = pilot["objectSid"]

    proof = deploy_svc.machine_proof_digest(token=token, computer_sid=sid, computer_guid="")
    enrolled = client.post(
        "/api/v1/agent/desktop/enroll",
        json={
            "machineEnrollmentToken": token,
            "adComputerSid": sid,
            "sidSource": "local_machine",
            "machineProof": proof,
            "computerName": "PILOT-PC",
            "deviceFingerprint": "login-disabled-owner-fp",
            "agentVersion": "0.1.0",
        },
    )
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["enrollmentMode"] == "machine_identity"
    assert enrolled.json()["qkaUserId"] == str(LOGIN_DISABLED_OWNER)
    assert enrolled.json()["employeeAssigned"] is True


def test_desktop_source_access_owner_login_and_reassign(client):
    headers = _admin(client)
    now = datetime.now(timezone.utc)
    device_id = uuid.uuid4()
    with client.app.state.session_factory() as session:
        _seed_login_disabled_owner(session)
        session.add(
            DesktopDevice(
                id=device_id,
                qka_user_id=ALLOWED_USER,
                device_key_hash="hash-" + device_id.hex,
                computer_name="WS-OWNER",
                device_fingerprint="fp-owner-1",
                status=DEVICE_ACTIVE,
                indexing_state=INDEX_IDLE,
                enrolled_at=now,
                created_at=now,
                updated_at=now,
                last_heartbeat_at=now,
            )
        )
        session.commit()

        owner = session.get(QkaUser, ALLOWED_USER)
        disabled = session.get(QkaUser, LOGIN_DISABLED_OWNER)
        stranger = session.get(QkaUser, DENIED_USER)
        device = session.get(DesktopDevice, device_id)

        assert authorize_desktop_source(session, owner, device) is True
        assert authorize_desktop_source(session, stranger, device) is False
        assert evaluate_desktop_source_access(session, stranger, device).denial_reason == (
            "desktop_source_not_owner"
        )

        # Reassign ownership → previous owner loses access; index/device row preserved.
        reassigned = client.post(
            f"/api/v1/admin/desktop-devices/{device_id}/assign-employee",
            headers=headers,
            json={"qkaUserId": str(LOGIN_DISABLED_OWNER)},
        )
        assert reassigned.status_code == 200, reassigned.text
        session.refresh(device)
        assert device.qka_user_id == LOGIN_DISABLED_OWNER
        assert authorize_desktop_source(session, owner, device) is False
        # Login-disabled owner keeps association but cannot retrieve through QKA.
        assert authorize_desktop_source(session, disabled, device) is False
        assert evaluate_desktop_source_access(session, disabled, device).denial_reason == (
            "qka_login_disabled"
        )

        cleared = client.post(
            f"/api/v1/admin/desktop-devices/{device_id}/assign-employee",
            headers=headers,
            json={"qkaUserId": None},
        )
        assert cleared.status_code == 200, cleared.text
        session.refresh(device)
        assert device.qka_user_id is None
        assert authorize_desktop_source(session, owner, device) is False
        assert evaluate_desktop_source_access(session, owner, device).denial_reason == (
            "desktop_source_unassigned"
        )
        # Unassign preserves device row (index history identity).
        assert session.get(DesktopDevice, device_id) is not None
