"""Incremental Sync/Resync indexing scope + two-stage progress persistence."""

from __future__ import annotations

from datetime import datetime, timezone

from qka_api.services import extraction as extraction_svc
from qka_api.services.synology import process_next_job
from qka_api.smb import ACL_CAPTURED, AclSnapshot, EnumeratedFile, FakeSmbGateway
from qka_shared.config import get_settings
from qka_shared.models import SynologyDocumentContent, SynologySyncJob
from sqlalchemy import select
from tests.conftest import SID_ALLOWED

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


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
    from qka_api.services.synology import JOB_QUEUED, JOB_SCANNING
    from qka_shared.models import SynologyExtractionQueue

    settings = client.app.state.settings
    with client.app.state.session_factory() as session:
        process_next_job(session, settings, client.app.state.smb_gateway)
        session.commit()
        for _ in range(100):
            now = datetime.now(timezone.utc)
            pending = session.execute(
                select(SynologyExtractionQueue).where(
                    SynologyExtractionQueue.status == extraction_svc.QUEUE_PENDING
                )
            ).scalars().all()
            for item in pending:
                item.next_attempt_at = now
            session.flush()
            n = extraction_svc.process_extraction_queue(
                session, settings, client.app.state.smb_gateway, max_items=10
            )
            extraction_svc._finalize_batches(session)
            session.commit()
            active = session.execute(
                select(SynologySyncJob).where(SynologySyncJob.status.in_([JOB_QUEUED, JOB_SCANNING]))
            ).scalars().first()
            if active is None and n == 0:
                break


def test_connection_scope_indexes_outside_pilot_prefix(client, monkeypatch) -> None:
    monkeypatch.setenv("QKA_INDEXING_SCOPE_MODE", "connection")
    get_settings.cache_clear()
    client.app.state.settings = get_settings()

    headers = _admin(client)
    gateway = _gateway(client)
    saved = client.post(
        "/api/v1/admin/synology/connections",
        json={
            "name": "Scope NAS",
            "sharedFolderPath": r"\\192.168.110.11\eduard",
            "authMode": 1,
            "serviceAccountUserName": r".\qkai",
            "serviceAccountSecret": "nas-secret-never-echo",
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    connection_id = saved.json()["id"]
    assert client.post(f"/api/v1/admin/synology/connections/{connection_id}/test", headers=headers).json()["succeeded"]

    outside = _file(r"Projects\outside-pilot.txt", size=24)
    gateway.files = [outside]
    gateway.contents = {r"PROJECTS\OUTSIDE-PILOT.TXT": b"connection scope pineapple token"}

    started = client.post(
        "/api/v1/admin/synology/sync/start",
        json={"connectionId": connection_id},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    job_body = started.json()
    assert job_body["extractionBatchId"]
    assert job_body["runStage"] == "discovering"
    assert job_body["indexing"]["provisional"] is True

    # Overlap prevention while discovering/indexing.
    assert (
        client.post(
            "/api/v1/admin/synology/sync/start",
            json={"connectionId": connection_id},
            headers=headers,
        ).status_code
        == 409
    )

    _run_worker(client)
    done = client.get(f"/api/v1/admin/synology/sync/jobs/{job_body['id']}").json()
    assert done["status"] == 2
    assert done["runStage"] == "completed"
    assert done["enumerationComplete"] is True
    assert done["indexing"]["indexed"] >= 1
    assert done["indexing"]["failed"] == 0
    assert done["percentComplete"] == 100.0

    with client.app.state.session_factory() as session:
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.file_name == "outside-pilot.txt")
        ).scalar_one()
        assert content.status == 1
        assert "pineapple" in (content.extracted_text or "")

    # Unchanged resync should skip re-extraction (unchanged count, no new failures).
    gateway.files = [outside]
    resync = client.post(
        "/api/v1/admin/synology/sync/start",
        json={"connectionId": connection_id},
        headers=headers,
    )
    assert resync.status_code == 200
    _run_worker(client)
    resync_job = client.get(f"/api/v1/admin/synology/sync/jobs/{resync.json()['id']}").json()
    assert resync_job["status"] == 2
    assert resync_job["filesUnchanged"] >= 1
    assert resync_job["indexing"]["queued"] == 0

    get_settings.cache_clear()


def test_missing_index_retries_on_unchanged_file(client, monkeypatch) -> None:
    monkeypatch.setenv("QKA_INDEXING_SCOPE_MODE", "connection")
    get_settings.cache_clear()
    client.app.state.settings = get_settings()

    headers = _admin(client)
    gateway = _gateway(client)
    saved = client.post(
        "/api/v1/admin/synology/connections",
        json={
            "name": "Retry NAS",
            "sharedFolderPath": r"\\192.168.110.11\eduard",
            "authMode": 1,
            "serviceAccountUserName": r".\qkai",
            "serviceAccountSecret": "nas-secret-never-echo",
        },
        headers=headers,
    )
    connection_id = saved.json()["id"]
    client.post(f"/api/v1/admin/synology/connections/{connection_id}/test", headers=headers)
    sample = _file(r"Docs\retry-me.txt", size=16)
    gateway.files = [sample]
    gateway.contents = {r"DOCS\RETRY-ME.TXT": b"retry pineapple token"}

    started = client.post(
        "/api/v1/admin/synology/sync/start",
        json={"connectionId": connection_id},
        headers=headers,
    )
    _run_worker(client)
    job_id = started.json()["id"]
    assert client.get(f"/api/v1/admin/synology/sync/jobs/{job_id}").json()["status"] == 2

    with client.app.state.session_factory() as session:
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.file_name == "retry-me.txt")
        ).scalar_one()
        content.status = 0
        content.extracted_text = None
        content.safe_error = "extractor_v=0|forced missing"
        session.commit()

    gateway.files = [sample]
    resync = client.post(
        "/api/v1/admin/synology/sync/start",
        json={"connectionId": connection_id},
        headers=headers,
    )
    _run_worker(client)
    again = client.get(f"/api/v1/admin/synology/sync/jobs/{resync.json()['id']}").json()
    assert again["status"] == 2
    assert again["indexing"]["queued"] >= 1 or again["indexing"]["indexed"] >= 1
    with client.app.state.session_factory() as session:
        content = session.execute(
            select(SynologyDocumentContent).where(SynologyDocumentContent.file_name == "retry-me.txt")
        ).scalar_one()
        assert content.status == 1
        assert "pineapple" in (content.extracted_text or "")

    get_settings.cache_clear()
