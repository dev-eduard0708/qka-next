from __future__ import annotations

from datetime import datetime, timezone

from qka_api.services.synology import process_next_job
from qka_api.smb import ACL_CAPTURED, AclSnapshot, EnumeratedFile, FakeSmbGateway
from qka_shared.models import SynologyDocumentContent, SynologyFileInventory
from qka_shared.unc import UncError, parse_unc
from sqlalchemy import select
from tests.conftest import ALLOWED_USER, FILE_ID, SID_ALLOWED, auth_cookies

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _csrf(client) -> dict[str, str]:
    token = client.cookies.get("qka_next_csrf")
    return {"X-CSRF-Token": token} if token else {}


def _admin(client):
    login = client.post("/api/v1/auth/login", json={"username": "super", "password": "123"})
    assert login.status_code == 200, login.text
    return _csrf(client)


def _file(relative: str, size: int = 12, write: datetime | None = None, ext: str | None = None) -> EnumeratedFile:
    name = relative.rsplit("\\", 1)[-1]
    extension = ext if ext is not None else (name.rsplit(".", 1)[-1] if "." in name else "")
    return EnumeratedFile(
        relative_path=relative,
        file_name=name,
        extension=extension,
        size_bytes=size,
        last_write_utc=write or NOW,
        acl=AclSnapshot(
            True,
            ACL_CAPTURED,
            [{"Identity": SID_ALLOWED, "Rights": "Read", "AccessControlType": "Allow"}],
            None,
        ),
    )


def _gateway(client) -> FakeSmbGateway:
    gateway = FakeSmbGateway()
    client.app.state.smb_gateway = gateway
    return gateway


def _run_worker(client) -> None:
    settings = client.app.state.settings
    with client.app.state.session_factory() as session:
        process_next_job(session, settings, client.app.state.smb_gateway)
        session.commit()


def test_unc_parse_and_reject_escape() -> None:
    host, share, folder, display = parse_unc(r"\\192.168.110.11\eduard\EDUARDTESTCLOUD")
    assert host == "192.168.110.11"
    assert share == "eduard"
    assert folder == "EDUARDTESTCLOUD"
    assert display.startswith("\\\\192.168.110.11\\eduard")
    try:
        parse_unc(r"\\192.168.110.11\eduard\..\secret")
        raise AssertionError("expected UncError")
    except UncError:
        pass


def test_non_admin_cannot_administer_synology(client) -> None:
    cookies = auth_cookies(ALLOWED_USER)
    listed = client.get("/api/v1/admin/synology/connection", cookies=cookies)
    assert listed.status_code == 403
    started = client.post("/api/v1/admin/synology/sync/start", cookies=cookies, headers={"X-CSRF-Token": "x"})
    assert started.status_code in {401, 403}


def test_connection_crud_hides_secret_and_tests_smb(client) -> None:
    headers = _admin(client)
    _gateway(client)
    saved = client.post(
        "/api/v1/admin/synology/connections",
        json={
            "name": "Lab NAS",
            "sharedFolderPath": r"\\192.168.110.11\eduard",
            "authMode": 1,
            "serviceAccountUserName": r".\qkai",
            "serviceAccountSecret": "nas-secret-never-echo",
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    connection_id = body["id"]
    assert body["name"] == "Lab NAS"
    assert body["hostName"] == "192.168.110.11"
    assert body["serviceAccountSecretConfigured"] is True
    assert body["serviceAccountUserName"] == r".\qkai"
    dumped = saved.text.lower()
    assert "nas-secret-never-echo" not in dumped
    assert "password" not in body

    listed = client.get("/api/v1/admin/synology/connections")
    assert listed.status_code == 200
    assert any(row["id"] == connection_id for row in listed.json())

    second = client.post(
        "/api/v1/admin/synology/connections",
        json={
            "name": "Finance",
            "sharedFolderPath": r"\\192.168.110.11\finance",
            "authMode": 1,
            "serviceAccountUserName": r".\qkai",
            "serviceAccountSecret": "nas-secret-never-echo",
        },
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert second.json()["name"] == "Finance"
    assert len(client.get("/api/v1/admin/synology/connections").json()) >= 2

    fetched = client.get(f"/api/v1/admin/synology/connections/{connection_id}")
    assert fetched.status_code == 200
    assert "nas-secret-never-echo" not in fetched.text
    assert fetched.json()["serviceAccountSecretConfigured"] is True

    tested = client.post(f"/api/v1/admin/synology/connections/{connection_id}/test", headers=headers)
    assert tested.status_code == 200, tested.text
    result = tested.json()
    assert result["succeeded"] is True
    assert result["port445Reachable"] is True
    assert result["authenticatedShareAccess"] is True
    assert "nas-secret" not in tested.text

    updated = client.put(
        f"/api/v1/admin/synology/connections/{connection_id}",
        json={
            "name": "Lab NAS",
            "sharedFolderPath": r"\\192.168.110.11\eduard",
            "authMode": 1,
            "serviceAccountUserName": r".\qkai",
        },
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["serviceAccountSecretConfigured"] is True
    assert "nas-secret-never-echo" not in updated.text


def test_sync_resync_extraction_and_incomplete_scan_safety(client) -> None:
    headers = _admin(client)
    gateway = _gateway(client)
    saved = client.post(
        "/api/v1/admin/synology/connections",
        json={
            "name": "Lab NAS",
            "sharedFolderPath": r"\\192.168.110.11\eduard",
            "authMode": 1,
            "serviceAccountUserName": r".\qkai",
            "serviceAccountSecret": "nas-secret-never-echo",
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    connection_id = saved.json()["id"]
    tested = client.post(f"/api/v1/admin/synology/connections/{connection_id}/test", headers=headers)
    assert tested.json()["succeeded"] is True

    first = _file(r"EDUARDTESTCLOUD\notes.txt", size=20)
    second = _file(r"EDUARDTESTCLOUD\deck.pptx", size=40, ext="pptx")
    gateway.files = [first, second]
    gateway.contents = {
        r"EDUARDTESTCLOUD\NOTES.TXT": b"searchable pineapple token for postgres",
        r"EDUARDTESTCLOUD\DECK.PPTX": b"not-extracted",
    }

    started = client.post(
        "/api/v1/admin/synology/sync/start",
        json={"connectionId": connection_id},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    duplicate = client.post(
        "/api/v1/admin/synology/sync/start",
        json={"connectionId": connection_id},
        headers=headers,
    )
    assert duplicate.status_code == 409

    _run_worker(client)
    job = client.get(f"/api/v1/admin/synology/sync/jobs/{started.json()['id']}")
    assert job.status_code == 200
    body = job.json()
    assert body["status"] == 2
    assert body["enumerationComplete"] is True
    assert body["filesNew"] >= 2
    assert body["filesFailed"] == 0
    assert body["percentComplete"] == 100.0

    inventory = client.get(
        "/api/v1/admin/synology/inventory",
        params={"page": 1, "pageSize": 50, "connectionId": connection_id},
    )
    assert inventory.status_code == 200
    items = inventory.json()["items"]
    names = {row["fileName"] for row in items}
    assert "notes.txt" in names
    assert "deck.pptx" in names
    ppt = next(row for row in items if row["fileName"] == "deck.pptx")
    notes = next(row for row in items if row["fileName"] == "notes.txt")
    assert ppt["indexingState"] == "unsupported"
    assert notes["indexingState"] == "indexed"

    stats = client.get("/api/v1/admin/synology/sync/stats", params={"connectionId": connection_id})
    assert stats.status_code == 200
    counts = stats.json()
    assert counts["totalFiles"] >= 2
    assert counts["other"] >= 1
    assert counts["powerPoint"] >= 1

    with client.app.state.session_factory() as session:
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.file_name == "notes.txt")
        ).scalar_one()
        assert "pineapple" in (content.extracted_text or "")
        assert content.status == 1

    allowed = auth_cookies(ALLOWED_USER)
    saved_cookies = dict(client.cookies)
    client.cookies.clear()
    client.cookies.set("qka_next_session", allowed["qka_next_session"])
    search = client.get("/api/v1/search", params={"q": "pineapple"})
    client.cookies.clear()
    for name, value in saved_cookies.items():
        client.cookies.set(name, value)
    assert search.status_code == 200
    hits = search.json()["results"]
    assert any("notes.txt" in hit["title"] for hit in hits)

    changed = _file(r"EDUARDTESTCLOUD\notes.txt", size=30, write=datetime(2026, 11, 1, 13, 0, tzinfo=timezone.utc))
    added = _file(r"EDUARDTESTCLOUD\new.csv", size=8, ext="csv")
    gateway.files = [changed, added]
    gateway.contents[r"EDUARDTESTCLOUD\NOTES.TXT"] = b"searchable pineapple token updated"
    gateway.contents[r"EDUARDTESTCLOUD\NEW.CSV"] = b"colA,colB\n1,2"

    resync = client.post(
        "/api/v1/admin/synology/sync/start",
        json={"connectionId": connection_id},
        headers=headers,
    )
    assert resync.status_code == 200
    assert resync.json()["trigger"] == 1
    _run_worker(client)
    resync_job = client.get(f"/api/v1/admin/synology/sync/jobs/{resync.json()['id']}").json()
    assert resync_job["status"] == 2
    assert resync_job["filesNew"] >= 1
    assert resync_job["filesModified"] >= 1
    assert resync_job["filesRemoved"] >= 1

    after = client.get(
        "/api/v1/admin/synology/inventory",
        params={"search": "deck", "page": 1, "pageSize": 50, "connectionId": connection_id},
    ).json()
    assert after["totalCount"] == 0

    with client.app.state.session_factory() as session:
        present = session.execute(select(SynologyFileInventory).where(SynologyFileInventory.is_present.is_(True))).scalars().all()
        names = {row.file_name for row in present}
        assert "notes.txt" in names
        assert "new.csv" in names
        assert "deck.pptx" not in names
        original = session.get(SynologyFileInventory, FILE_ID)
        if original is not None:
            original.is_present = True
            session.commit()

    stale = _file(r"EDUARDTESTCLOUD\keep.txt")
    gone = _file(r"EDUARDTESTCLOUD\temp.txt")
    gateway.files = [stale, gone]
    gateway.raise_after = 1
    interrupted = client.post(
        "/api/v1/admin/synology/sync/start",
        json={"connectionId": connection_id},
        headers=headers,
    )
    assert interrupted.status_code == 200
    _run_worker(client)
    failed = client.get(f"/api/v1/admin/synology/sync/jobs/{interrupted.json()['id']}").json()
    assert failed["status"] == 3
    assert failed["enumerationComplete"] is False
    assert failed["filesRemoved"] == 0
    with client.app.state.session_factory() as session:
        remaining = session.execute(
            select(SynologyFileInventory).where(
                SynologyFileInventory.file_name == "new.csv",
                SynologyFileInventory.is_present.is_(True),
            )
        ).scalar_one_or_none()
        assert remaining is not None
