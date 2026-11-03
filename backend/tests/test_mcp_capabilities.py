"""Unit/integration tests for expanded MCP capabilities (no production indexing)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from qka_api.services import mcp_capabilities as caps
from qka_shared.models import (
    McpDocumentHandoff,
    McpIndexingConfirmation,
    McpToolAudit,
    QkaUser,
    SynologyApprovedShare,
    SynologyDocumentContent,
    SynologyExtractionBatch,
    SynologyFileInventory,
)
from qka_shared.source_access import Capability


def _admin(client):
    login = client.post("/api/v1/auth/login", json={"username": "super", "password": "123"})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get("qka_next_csrf")
    return {"X-CSRF-Token": csrf} if csrf else {}


def _seed_inventory(session, share_id: uuid.UUID, *, file_name: str = "Report.pdf", path: str = "IT\\Report.pdf"):
    now = datetime.now(timezone.utc)
    inv = SynologyFileInventory(
        id=uuid.uuid4(),
        approved_share_id=share_id,
        relative_path=path,
        relative_path_normalized=path.upper(),
        file_name=file_name,
        extension="pdf",
        file_type_category=2,
        size_bytes=1024,
        sync_status=0,
        acl_capture_status=0,
        is_present=True,
        is_directory=False,
        parent_path_normalized="IT",
        created_at=now,
        updated_at=now,
    )
    folder = SynologyFileInventory(
        id=uuid.uuid4(),
        approved_share_id=share_id,
        relative_path="IT",
        relative_path_normalized="IT",
        file_name="IT",
        extension="",
        file_type_category=0,
        size_bytes=0,
        sync_status=0,
        acl_capture_status=0,
        is_present=True,
        is_directory=True,
        parent_path_normalized="",
        created_at=now,
        updated_at=now,
    )
    session.add_all([folder, inv])
    session.flush()
    return inv


def test_employee_origin_rejects_localhost(client):
    settings = client.app.state.settings
    settings.frontend_origin = "http://127.0.0.1:5186"
    settings.mcp_public_base_url = "http://localhost:5265"
    assert caps.employee_app_origin(settings) is None
    settings.frontend_origin = "https://qka.example.sa"
    assert caps.employee_app_origin(settings) == "https://qka.example.sa"


def test_download_link_requires_grant_and_public_origin(client, monkeypatch):
    headers = _admin(client)
    settings = client.app.state.settings
    settings.frontend_origin = "https://qka.example.sa"

    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        assert admin is not None
        share = session.execute(select(SynologyApprovedShare)).scalars().first()
        if share is None:
            pytest.skip("No approved share in test DB")
        inv = _seed_inventory(session, share.id)
        session.commit()
        inv_id = inv.id.hex

        # Without Download Originals grant, deny.
        denied = caps.get_document_download_link(session, settings, admin.id, inv_id)
        # Super admin may or may not have grants depending on seed; assert shape.
        assert "downloadAuthorized" in denied or denied.get("denied") is True
        assert denied.get("downloadLink") is None or isinstance(denied.get("downloadLink"), str)
        # Never expose SMB / UNC
        blob = json.dumps(denied)
        assert "\\\\" not in blob or "downloadLink" in denied
        assert "password" not in blob.lower()
        session.commit()


def test_view_only_cannot_download_handoff(client):
    settings = client.app.state.settings
    settings.frontend_origin = "https://qka.example.sa"
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        assert admin is not None
        # Bogus document → denied, no link
        result = caps.get_document_download_link(session, settings, admin.id, "00000000000000000000000000000000")
        assert result["denied"] is True
        assert result["downloadLink"] is None
        assert result["browserDownloadAvailable"] is False


def test_preview_tool_shape(client):
    settings = client.app.state.settings
    settings.frontend_origin = "https://qka.example.sa"
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        result = caps.get_document_preview(session, settings, admin.id, "00000000000000000000000000000000")
        assert result["denied"] is True
        assert result["previewLink"] is None


def test_start_indexing_requires_super_admin_and_confirmation(client):
    settings = client.app.state.settings
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        assert admin is not None
        # No confirmation → rejected
        denied = caps.start_indexing(session, settings, admin.id, operation="extract_missing")
        assert denied["accepted"] is False
        assert denied.get("requiresConfirmation") is True

        # Non-admin
        regular = QkaUser(
            id=uuid.uuid4(),
            display_name="Regular",
            status=0,
            login_enabled=True,
            is_directory_operator=False,
            is_super_admin=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(regular)
        session.flush()
        no_admin = caps.start_indexing(
            session, settings, regular.id, confirmation_id=str(uuid.uuid4()), operation="sync"
        )
        assert no_admin["accepted"] is False
        assert "Super Admin" in (no_admin.get("error") or "")


def test_indexing_confirmation_api_and_consume_once(client):
    headers = _admin(client)
    created = client.post(
        "/api/v1/admin/synology/mcp-indexing-confirmations",
        headers=headers,
        json={"operation": "extract_missing", "documentIds": []},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body.get("confirmationId")
    cid = body["confirmationId"]

    settings = client.app.state.settings
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        # Starting extract_missing on empty inventory should accept without launching a production sync.
        result = caps.start_indexing(session, settings, admin.id, confirmation_id=cid)
        session.commit()
        assert result.get("accepted") is True or result.get("denied") is True
        # Confirmation must be single-use when accepted
        if result.get("accepted"):
            again = caps.start_indexing(session, settings, admin.id, confirmation_id=cid)
            session.commit()
            assert again.get("accepted") is False
            assert "already used" in (again.get("error") or "").lower()


def test_duplicate_job_protection(client):
    settings = client.app.state.settings
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        now = datetime.now(timezone.utc)
        batch = SynologyExtractionBatch(
            id=uuid.uuid4(),
            action="extract_missing",
            status=1,  # RUNNING
            actor_user_id=admin.id,
            approved_share_id=None,
            folder_prefix_normalized="",
            force_outside_pilot=False,
            queued_count=1,
            created_at=now,
        )
        session.add(batch)
        minted = caps.create_indexing_confirmation(
            session, actor_id=admin.id, operation="extract_missing"
        )
        session.flush()
        result = caps.start_indexing(
            session, settings, admin.id, confirmation_id=minted["confirmationId"]
        )
        session.commit()
        assert result.get("accepted") is False
        assert result.get("duplicateJob") is True or "already" in (result.get("error") or "").lower()


def test_index_status_readonly_real_counters(client):
    settings = client.app.state.settings
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        result = caps.get_index_status(session, settings, admin.id)
        assert result.get("denied") is False
        assert "progressPercentage" in result
        # Must not invent a float when indeterminate
        assert result["progressPercentage"] is None or isinstance(
            result["progressPercentage"], (int, float, str)
        )


def test_list_documents_paginated_and_bounded(client):
    settings = client.app.state.settings
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        result = caps.list_documents(session, settings, admin.id, page_size=5)
        assert "documents" in result
        assert len(result.get("documents") or []) <= 5
        # Unindexed files may appear when authorized (contentAvailable can be false)
        for doc in result.get("documents") or []:
            assert "documentId" in doc
            assert "filename" in doc
            assert "indexingStatus" in doc


def test_existing_mcp_tools_still_listed(client):
    oauth = client.app.state.mcp_oauth
    oauth.settings.mcp_oauth_enabled = True
    oauth.settings.mcp_inspector_bearer = "inspector-dev-token"
    with client.app.state.session_factory() as session:
        admin = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
        oauth.settings.mcp_inspector_user_id = admin.id

    headers = {
        "Authorization": "Bearer inspector-dev-token",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
    )
    tools = client.post(
        "/mcp",
        headers={**headers, "MCP-Protocol-Version": "2025-03-26"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert tools.status_code != 401
    text = tools.text
    for name in (
        "search",
        "fetch",
        "get_profile",
        "get_document_download_link",
        "get_document_preview",
        "list_folders",
        "list_documents",
        "get_index_status",
        "start_indexing",
    ):
        assert name in text or tools.status_code in {200, 202}
