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
    assert "embed" in issued.json()["installHint"].lower() or "MSI" in issued.json()["installHint"]

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


def test_agent_readonly_module_unit():
    from qka_desktop_agent.readonly import is_excluded_path
    from pathlib import Path

    assert is_excluded_path(Path(r"C:\Users\eduard\AppData\Local\Temp")) is True
    assert is_excluded_path(Path(r"C:\Windows\System32")) is True
    assert is_excluded_path(Path(r"C:\QKA-Pilot-Docs\Contracts")) is False
