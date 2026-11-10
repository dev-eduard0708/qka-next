"""Desktop device inventory upsert + folder cache."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from qka_api.services import desktop_inventory as inv_svc
from qka_api.services.desktop_agent import DEVICE_ACTIVE, INDEX_IDLE
from qka_shared.models import DesktopDevice


def _admin(client):
    login = client.post("/api/v1/auth/login", json={"username": "super", "password": "123"})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get("qka_next_csrf")
    return {"X-CSRF-Token": csrf} if csrf else {}


def _seed_device(session, *, computer_name="INV-PC"):
    now = datetime.now(timezone.utc)
    device_id = uuid.uuid4()
    session.add(
        DesktopDevice(
            id=device_id,
            qka_user_id=None,
            device_key_hash="inv-hash-" + device_id.hex,
            computer_name=computer_name,
            device_fingerprint="fp-" + device_id.hex[:12],
            domain_joined_name="QEHC",
            agent_version="0.1.3",
            status=DEVICE_ACTIVE,
            indexing_state=INDEX_IDLE,
            enrolled_at=now,
            created_at=now,
            updated_at=now,
            last_heartbeat_at=now,
        )
    )
    session.commit()
    return device_id


def test_inventory_upsert_and_admin_read(client):
    headers = _admin(client)
    with client.app.state.session_factory() as session:
        device_id = _seed_device(session)
        device = session.get(DesktopDevice, device_id)
        due = inv_svc.inventory_due(session, device)
        assert due["hardwareDue"] is True
        assert due["volumesDue"] is True

        result = inv_svc.upsert_inventory(
            session,
            device,
            payload={
                "agentVersion": "0.1.3",
                "includeHardware": True,
                "includeVolumes": True,
                "hardware": {
                    "hostname": "WSIT02",
                    "domainName": "QEHC",
                    "osEdition": "Windows 11 Pro",
                    "osVersion": "23H2",
                    "osBuild": "22631",
                    "manufacturer": "Dell",
                    "model": "OptiPlex",
                    "serialNumber": "ABC123",
                    "cpuModel": "Intel Core",
                    "cpuPhysicalCores": 4,
                    "cpuLogicalProcessors": 8,
                    "ramBytes": 16 * 1024**3,
                },
                "disks": [{"index": 0, "model": "NVMe", "sizeBytes": 512 * 1024**3}],
                "volumes": [
                    {
                        "letter": "C:",
                        "rootPath": "C:\\",
                        "label": "System",
                        "fileSystem": "NTFS",
                        "capacityBytes": 500 * 1024**3,
                        "freeBytes": 200 * 1024**3,
                        "usedBytes": 300 * 1024**3,
                        "accessible": True,
                    }
                ],
                "knownFolders": [
                    {
                        "name": "Desktop",
                        "path": "C:\\Users\\eduard\\Desktop",
                        "kind": "known_folder",
                        "accessible": True,
                        "hasChildren": True,
                        "detail": "",
                    }
                ],
            },
        )
        session.commit()
        assert result["ok"] is True
        assert result["inventory"]["hostname"] == "WSIT02"
        assert result["inventory"]["volumes"][0]["letter"] == "C:"

    listed = client.get(f"/api/v1/admin/desktop-devices/{device_id}/inventory", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["inventory"]["hostname"] == "WSIT02"
    assert listed.json()["inventory"]["ramBytes"] > 0

    roots = client.get(f"/api/v1/admin/desktop-devices/{device_id}/browse-roots", headers=headers)
    assert roots.status_code == 200, roots.text
    body = roots.json()
    assert body["cacheHit"] is True
    assert any(n.get("kind") == "volume" for n in body["nodes"])
    assert any(n.get("name") == "Desktop" for n in body["nodes"])

    # Second upsert with same payload is change-detection no-op for content hash.
    with client.app.state.session_factory() as session:
        device = session.get(DesktopDevice, device_id)
        again = inv_svc.upsert_inventory(
            session,
            device,
            payload={
                "agentVersion": "0.1.3",
                "includeHardware": True,
                "includeVolumes": True,
                "hardware": {
                    "hostname": "WSIT02",
                    "domainName": "QEHC",
                    "osEdition": "Windows 11 Pro",
                    "osVersion": "23H2",
                    "osBuild": "22631",
                    "manufacturer": "Dell",
                    "model": "OptiPlex",
                    "serialNumber": "ABC123",
                    "cpuModel": "Intel Core",
                    "cpuPhysicalCores": 4,
                    "cpuLogicalProcessors": 8,
                    "ramBytes": 16 * 1024**3,
                },
                "disks": [{"index": 0, "model": "NVMe", "sizeBytes": 512 * 1024**3}],
                "volumes": [
                    {
                        "letter": "C:",
                        "rootPath": "C:\\",
                        "label": "System",
                        "fileSystem": "NTFS",
                        "capacityBytes": 500 * 1024**3,
                        "freeBytes": 200 * 1024**3,
                        "usedBytes": 300 * 1024**3,
                        "accessible": True,
                    }
                ],
                "knownFolders": [
                    {
                        "name": "Desktop",
                        "path": "C:\\Users\\eduard\\Desktop",
                        "kind": "known_folder",
                        "accessible": True,
                        "hasChildren": True,
                        "detail": "",
                    }
                ],
            },
        )
        session.commit()
        assert again["changed"] is False


def test_inventory_refresh_flag(client):
    headers = _admin(client)
    with client.app.state.session_factory() as session:
        device_id = _seed_device(session, computer_name="INV-REFRESH")
    req = client.post(f"/api/v1/admin/desktop-devices/{device_id}/inventory/refresh", headers=headers)
    assert req.status_code == 200, req.text
    with client.app.state.session_factory() as session:
        device = session.get(DesktopDevice, device_id)
        due = inv_svc.inventory_due(session, device)
        assert due["forceRefresh"] is True
        assert due["hardwareDue"] is True
