"""Admin folder browse request/result over outbound agent poll."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from qka_shared.models import DesktopDevice, DEVICE_ACTIVE
from tests.conftest import ALLOWED_USER, SID_ALLOWED
from tests.test_desktop_agent import _admin


def _enroll(client, headers):
    issued = client.post(
        "/api/v1/admin/desktop-devices/enrollment-codes",
        headers=headers,
        json={"qkaUserId": str(ALLOWED_USER), "label": "browse"},
    )
    assert issued.status_code == 200, issued.text
    enrolled = client.post(
        "/api/v1/agent/desktop/enroll",
        json={
            "enrollmentCode": issued.json()["enrollmentCode"],
            "computerName": "BROWSE-PC",
            "deviceFingerprint": "browse-fp-1",
            "agentVersion": "0.1.0",
        },
    )
    assert enrolled.status_code == 200, enrolled.text
    return enrolled.json()["deviceId"], enrolled.json()["deviceKey"]


def test_folder_browse_offline_rejected(client):
    headers = _admin(client)
    device_id, _key = _enroll(client, headers)
    # Force offline by clearing heartbeat into the past.
    with client.app.state.session_factory() as session:
        device = session.get(DesktopDevice, __import__("uuid").UUID(device_id))
        assert device is not None
        device.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        session.commit()

    denied = client.post(
        f"/api/v1/admin/desktop-devices/{device_id}/folder-browse",
        headers=headers,
        json={"parentPath": ""},
    )
    assert denied.status_code == 400
    assert "offline" in denied.json()["detail"].lower() or "heart" in denied.json()["detail"].lower()


def test_folder_browse_request_policy_and_result(client):
    headers = _admin(client)
    device_id, device_key = _enroll(client, headers)
    auth = {"X-QKA-Device-Id": device_id, "X-QKA-Device-Key": device_key}

    # Heartbeat keeps device online.
    assert client.post("/api/v1/agent/desktop/heartbeat", headers=auth, json={"health": "ok"}).status_code == 200

    req = client.post(
        f"/api/v1/admin/desktop-devices/{device_id}/folder-browse",
        headers=headers,
        json={"parentPath": ""},
    )
    assert req.status_code == 200, req.text
    browse_id = req.json()["id"]
    assert req.json()["employeeAdSid"] == SID_ALLOWED

    policy = client.get("/api/v1/agent/desktop/policy", headers=auth)
    assert policy.status_code == 200
    pending = policy.json().get("pendingFolderBrowse") or []
    assert any(p["id"] == browse_id for p in pending)
    assert policy.json()["employeeAdSid"] == SID_ALLOWED

    result_post = client.post(
        f"/api/v1/agent/desktop/folder-browse/{browse_id}/result",
        headers=auth,
        json={
            "nodes": [
                {
                    "name": "Documents",
                    "path": r"C:\Users\pilot\Documents",
                    "kind": "known_folder",
                    "accessible": True,
                    "hasChildren": True,
                    "detail": "",
                }
            ],
            "error": "",
        },
    )
    assert result_post.status_code == 200, result_post.text

    got = client.get(
        f"/api/v1/admin/desktop-devices/{device_id}/folder-browse/{browse_id}",
        headers=headers,
    )
    assert got.status_code == 200
    assert got.json()["status"] == "completed"
    assert got.json()["nodes"][0]["name"] == "Documents"
    # No file content fields accepted
    assert "content" not in got.json()["nodes"][0]

    # Save selected folders + policy delivery
    saved = client.put(
        f"/api/v1/admin/desktop-devices/{device_id}/folders",
        headers=headers,
        json={"folders": [{"path": r"C:\Users\pilot\Documents", "includeSubfolders": True}]},
    )
    assert saved.status_code == 200
    policy2 = client.get("/api/v1/agent/desktop/policy", headers=auth)
    assert any(f["path"].endswith("Documents") for f in policy2.json()["approvedFolders"])
