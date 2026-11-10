"""Windows Desktop Agent pilot — server enrollment/policy/progress (no live PC claims)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from qka_shared.models import DesktopDevice, ExternalIdentity, QkaUser
from tests.conftest import ALLOWED_USER, GOOGLE_ONLY_USER, auth_cookies


def _admin(client):
    login = client.post("/api/v1/auth/login", json={"username": "super", "password": "123"})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get("qka_next_csrf")
    return {"X-CSRF-Token": csrf} if csrf else {}


def test_enrollment_requires_ad_linked_user(client):
    headers = _admin(client)
    # GOOGLE_ONLY_USER has Google but no AD identity in seed.
    denied = client.post(
        "/api/v1/admin/desktop-devices/enrollment-codes",
        headers=headers,
        json={"qkaUserId": str(GOOGLE_ONLY_USER)},
    )
    assert denied.status_code == 400
    assert "AD-linked" in denied.json()["detail"]


def test_install_register_index_then_assign_ownership(client):
    """Install → Register → Approve/Index without employee → Assign ownership later."""
    headers = _admin(client)
    issued = client.post(
        "/api/v1/admin/desktop-devices/enrollment-codes",
        headers=headers,
        json={"label": "device-only"},
    )
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["qkaUserId"] is None
    assert body["registrationMode"] == "device_registration"
    code = body["enrollmentCode"]

    enrolled = client.post(
        "/api/v1/agent/desktop/enroll",
        json={
            "enrollmentCode": code,
            "computerName": "UNASSIGNED-PC",
            "deviceFingerprint": "unassigned-pc-fp-1",
            "domainJoinedName": "QEHC",
            "agentVersion": "0.1.0",
        },
    )
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["employeeAssigned"] is False
    assert enrolled.json()["qkaUserId"] is None
    assert enrolled.json()["indexingEnabled"] is False
    device_id = enrolled.json()["deviceId"]
    device_key = enrolled.json()["deviceKey"]
    auth = {"X-QKA-Device-Id": device_id, "X-QKA-Device-Key": device_key}

    hb = client.post(
        "/api/v1/agent/desktop/heartbeat",
        headers=auth,
        json={"health": "ok", "agentVersion": "0.1.0"},
    )
    assert hb.status_code == 200

    policy = client.get("/api/v1/agent/desktop/policy", headers=auth)
    assert policy.status_code == 200
    assert policy.json()["employeeAssigned"] is False
    assert policy.json()["command"] == "pause"
    assert policy.json()["indexingEnabled"] is False
    assert policy.json()["approvedFolders"] == []
    assert policy.json()["sourceAccessRestricted"] is True

    listed = client.get("/api/v1/admin/desktop-devices", headers=headers)
    row = next(i for i in listed.json()["items"] if i["id"] == device_id)
    assert row["employeeAssigned"] is False
    assert row["employeeName"] == "Unassigned"
    assert row["sourceAccessRestricted"] is True
    assert row["folderBrowseAvailable"] is True
    # Computer column must be the workstation hostname, never domain NetBIOS.
    assert row["workstationHostname"] == "UNASSIGNED-PC"
    assert row["computerName"] == "UNASSIGNED-PC"
    assert row["domainJoinedName"] == "QEHC"
    assert row["computerName"].upper() != "QEHC"

    # Start without folders still blocked; folders/index allowed while unassigned.
    start_denied = client.post(
        f"/api/v1/admin/desktop-devices/{device_id}/command",
        headers=headers,
        json={"command": "start"},
    )
    assert start_denied.status_code == 400

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
    assert started.status_code == 200
    assert started.json()["indexingState"] == "running"
    assert started.json()["sourceAccessRestricted"] is True

    assigned = client.post(
        f"/api/v1/admin/desktop-devices/{device_id}/assign-employee",
        headers=headers,
        json={"qkaUserId": str(ALLOWED_USER)},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["employeeAssigned"] is True
    assert assigned.json()["qkaUserId"] == str(ALLOWED_USER)
    assert assigned.json()["employeeAdSid"]
    # Ownership does not wipe approved folders / index command state.
    assert assigned.json()["folders"]
    assert assigned.json()["indexingState"] == "running"

    policy2 = client.get("/api/v1/agent/desktop/policy", headers=auth)
    assert policy2.json()["employeeAssigned"] is True
    assert policy2.json()["sourceAccessRestricted"] is False
    assert policy2.json()["indexingEnabled"] is True


def test_enroll_heartbeat_policy_progress_and_revoke(client):
    headers = _admin(client)
    # Ensure ALLOWED_USER has AD linked identity (seed includes it).
    with client.app.state.session_factory() as session:
        ad = session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.qka_user_id == ALLOWED_USER,
                ExternalIdentity.provider == 0,
            )
        ).scalar_one_or_none()
        assert ad is not None

    issued = client.post(
        "/api/v1/admin/desktop-devices/enrollment-codes",
        headers=headers,
        json={"qkaUserId": str(ALLOWED_USER), "label": "pilot-pc"},
    )
    assert issued.status_code == 200, issued.text
    code = issued.json()["enrollmentCode"]
    assert code
    hint = issued.json()["installHint"].lower()
    assert "device registration" in hint or "embed" in hint or "msi" in hint
    assert issued.json().get("registrationMode") == "employee_bound_fallback"

    enrolled = client.post(
        "/api/v1/agent/desktop/enroll",
        json={
            "enrollmentCode": code,
            "computerName": "PILOT-PC",
            "deviceFingerprint": "pilot-pc-fingerprint-1",
            "domainJoinedName": "QEHC",
            "agentVersion": "0.1.0",
        },
    )
    assert enrolled.status_code == 200, enrolled.text
    device_id = enrolled.json()["deviceId"]
    device_key = enrolled.json()["deviceKey"]
    assert enrolled.json()["contentUploadEnabled"] is False

    # Code is single-use.
    again = client.post(
        "/api/v1/agent/desktop/enroll",
        json={
            "enrollmentCode": code,
            "computerName": "PILOT-PC",
            "deviceFingerprint": "pilot-pc-fingerprint-2",
            "agentVersion": "0.1.0",
        },
    )
    assert again.status_code == 403

    auth = {"X-QKA-Device-Id": device_id, "X-QKA-Device-Key": device_key}
    hb = client.post("/api/v1/agent/desktop/heartbeat", headers=auth, json={"health": "ok", "agentVersion": "0.1.0"})
    assert hb.status_code == 200

    folders = client.put(
        f"/api/v1/admin/desktop-devices/{device_id}/folders",
        headers=headers,
        json={"folders": [{"path": r"C:\QKA-Pilot-Docs", "includeSubfolders": True}]},
    )
    assert folders.status_code == 200, folders.text
    assert folders.json()["folders"][0]["path"].endswith("QKA-Pilot-Docs")

    policy = client.get("/api/v1/agent/desktop/policy", headers=auth)
    assert policy.status_code == 200
    assert policy.json()["localIndexOnly"] is True
    assert policy.json()["requireReadOnlyVerified"] is True
    assert any(f["path"].endswith("QKA-Pilot-Docs") for f in policy.json()["approvedFolders"])

    progress = client.post(
        "/api/v1/agent/desktop/progress",
        headers=auth,
        json={
            "indexingState": "running",
            "readOnlyVerified": True,
            "readOnlyDetail": "Read-only access verified.",
            "filesTotal": 1250,
            "filesIndexed": 775,
            "filesPending": 470,
            "filesFailed": 5,
            "filesByType": {"pdf": 400, "docx": 200},
        },
    )
    assert progress.status_code == 200

    # Without read-only, running is forced to error.
    blocked = client.post(
        "/api/v1/agent/desktop/progress",
        headers=auth,
        json={
            "indexingState": "running",
            "readOnlyVerified": False,
            "readOnlyDetail": "Write access detected",
            "filesTotal": 10,
            "filesIndexed": 0,
        },
    )
    assert blocked.status_code == 200

    listed = client.get("/api/v1/admin/desktop-devices", headers=headers)
    assert listed.status_code == 200
    row = next(i for i in listed.json()["items"] if i["id"] == device_id)
    assert row["computerName"] == "PILOT-PC"
    assert row["readOnlyVerified"] is False
    assert row["indexingState"] == "error"
    assert row["gpoDeployment"]["supported"] is True

    started = client.post(
        f"/api/v1/admin/desktop-devices/{device_id}/command",
        headers=headers,
        json={"command": "start"},
    )
    assert started.status_code == 200
    assert started.json()["indexingState"] == "running"

    revoked = client.post(f"/api/v1/admin/desktop-devices/{device_id}/revoke", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    denied = client.get("/api/v1/agent/desktop/policy", headers=auth)
    assert denied.status_code == 401


def test_non_admin_cannot_manage_devices(client):
    listed = client.get("/api/v1/admin/desktop-devices", cookies=auth_cookies(ALLOWED_USER))
    assert listed.status_code == 403


def test_device_dto_prefers_ad_workstation_hostname(client):
    """Computer column uses verified AD workstation name; never domain NetBIOS."""
    from datetime import datetime, timezone

    from qka_api.services.desktop_agent import DEVICE_ACTIVE, INDEX_IDLE, device_dto
    from qka_shared.models import AdWorkstation, DesktopDevice

    now = datetime.now(timezone.utc)
    ws_id = uuid.uuid4()
    device_id = uuid.uuid4()
    with client.app.state.session_factory() as session:
        session.add(
            AdWorkstation(
                id=ws_id,
                connection_id=uuid.uuid4(),
                object_guid=str(uuid.uuid4()),
                object_sid="S-1-5-21-TEST-WSIT04",
                computer_name="WSIT04",
                dns_hostname="WSIT04.qehealth.local",
                is_present=True,
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
        )
        session.add(
            DesktopDevice(
                id=device_id,
                qka_user_id=None,
                device_key_hash="hash",
                computer_name="QEHC",
                device_fingerprint="wsit04-fp",
                workstation_id=ws_id,
                domain_joined_name="QEHC",
                agent_version="0.1.0",
                status=DEVICE_ACTIVE,
                indexing_state=INDEX_IDLE,
                enrolled_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        device = session.get(DesktopDevice, device_id)
        dto = device_dto(session, device)

    assert dto["workstationHostname"] == "WSIT04"
    assert dto["computerName"] == "WSIT04"
    assert dto["domainJoinedName"] == "QEHC"
    assert dto["computerName"].upper() != "QEHC"


def test_device_dto_blank_hostname_when_only_domain_netbios(client):
    from datetime import datetime, timezone

    from qka_api.services.desktop_agent import DEVICE_ACTIVE, INDEX_IDLE, device_dto
    from qka_shared.models import DesktopDevice

    now = datetime.now(timezone.utc)
    device_id = uuid.uuid4()
    with client.app.state.session_factory() as session:
        session.add(
            DesktopDevice(
                id=device_id,
                qka_user_id=None,
                device_key_hash="hash2",
                computer_name="QEHC",
                device_fingerprint="domain-only-fp",
                domain_joined_name="QEHC",
                agent_version="0.1.0",
                status=DEVICE_ACTIVE,
                indexing_state=INDEX_IDLE,
                enrolled_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        dto = device_dto(session, session.get(DesktopDevice, device_id))

    assert dto["workstationHostname"] == ""
    assert dto["computerName"] == ""


def test_agent_readonly_module_unit():
    from qka_desktop_agent.readonly import is_excluded_path
    from pathlib import Path

    assert is_excluded_path(Path(r"C:\Users\eduard\AppData\Local\Temp")) is True
    assert is_excluded_path(Path(r"C:\Windows\System32")) is True
    assert is_excluded_path(Path(r"C:\QKA-Pilot-Docs\Contracts")) is False
