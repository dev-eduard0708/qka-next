"""QKA-Next MCP Streamable HTTP tools (search, fetch, get_profile + browse/download/index)."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context, MCPServer
from pydantic import AnyHttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from qka_api.mcp_oauth import McpOAuthService, QkaTokenVerifier
from qka_api.services import documents as docs
from qka_api.services import mcp_capabilities as caps
from qka_shared.allowlist import evaluate_user
from qka_shared.chat_access import channel_deny_message, require_mcp_chat
from qka_shared.config import Settings
from qka_shared.models import ExternalIdentity, McpToolAudit, QkaUser

log = logging.getLogger("qka.mcp")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _caller_user_id() -> uuid.UUID | None:
    token = get_access_token()
    if token is None:
        return None
    raw = (token.claims or {}).get("qka_user_id") or token.subject
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def _client_tag() -> str:
    token = get_access_token()
    if token is None:
        return "anonymous"
    client = (token.client_id or "").strip().lower()
    auth = str((token.claims or {}).get("auth") or "")
    if "chatgpt.com" in client or client.startswith("https://chatgpt.com"):
        return "chatgpt"
    if auth == "inspector" or client == "qka-next-inspector":
        return "inspector"
    if client.startswith("qka-next-"):
        return "dcr"
    return "oauth"


def _audit(
    session: Session,
    *,
    tool: str,
    user_id: uuid.UUID | None,
    succeeded: bool,
    error_category: str | None,
    detail: str | None = None,
) -> None:
    tag = _client_tag()
    safe_detail = f"client={tag}"
    if detail:
        safe_detail = f"{safe_detail}; {detail}"[:500]
    session.add(
        McpToolAudit(
            id=uuid.uuid4(),
            tool_name=tool,
            qka_user_id=user_id,
            succeeded=succeeded,
            error_category=error_category,
            detail=safe_detail,
            created_at=_now(),
        )
    )
    session.flush()


def create_mcp_server(
    oauth: McpOAuthService,
    settings: Settings,
    lifespan: Callable[[MCPServer], AbstractAsyncContextManager[dict[str, Any]]],
) -> MCPServer:
    # Always require bearer auth on /mcp. Tokens are accepted only when OAuth/inspector is configured.
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(oauth.issuer),
        resource_server_url=AnyHttpUrl(oauth.resource),
        required_scopes=[oauth.documents_scope],
        validate_token_resource=True,
    )

    mcp = MCPServer(
        name="QKA-Next Synology",
        instructions=(
            "QKA-Next Synology document tools. "
            "search/fetch/browse/preview/download always apply the authenticated employee's ACL. "
            "start_indexing is Super Admin only and requires a QKA admin UI confirmation. "
            "Never trust tool arguments for identity."
        ),
        token_verifier=QkaTokenVerifier(oauth),
        auth=auth,
        lifespan=lifespan,
    )

    @mcp.tool(
        name="search",
        description="Search authorized Synology documents. Returns id/title/url/snippet for documents the bound QKA identity may read.",
    )
    def search(query: str, ctx: Context) -> str:
        factory = ctx.request_context.lifespan_context["session_factory"]
        cfg = ctx.request_context.lifespan_context["settings"]
        caller = _caller_user_id()
        with factory() as session:
            if caller is None:
                _audit(session, tool="search", user_id=None, succeeded=False, error_category="unauthenticated")
                session.commit()
                return json.dumps({"results": [], "error": "Unauthenticated."})
            channel = require_mcp_chat(session, caller)
            if not channel.allowed:
                msg = channel_deny_message(channel.reason)
                _audit(session, tool="search", user_id=caller, succeeded=False, error_category="mcp_channel_denied", detail=msg)
                session.commit()
                return json.dumps({"results": [], "error": msg})
            hits, error = docs.search_documents(session, cfg, caller, query)
            _audit(
                session,
                tool="search",
                user_id=caller,
                succeeded=error is None,
                error_category=None if error is None else "search_denied",
                detail=error,
            )
            session.commit()
            return json.dumps(
                {
                    "results": [
                        {
                            "id": hit.id,
                            "title": hit.title,
                            "url": hit.url,
                            "snippet": hit.snippet,
                            "relativePath": hit.relative_path,
                            "extension": hit.extension,
                            "fileType": hit.file_type,
                            "matchType": hit.match_type,
                            "contentAvailable": hit.content_available,
                            "indexingState": hit.indexing_state,
                        }
                        for hit in hits
                    ],
                    "error": error,
                }
            )

    @mcp.tool(
        name="fetch",
        description=(
            "Fetch authorized Synology document text by id from search. "
            "Use offset or chunkIndex to retrieve subsequent windows of longer documents "
            "(responses are bounded; follow nextOffset/nextChunkIndex). "
            "Returns contentUnavailable when extraction is missing/pending/failed. "
            "OCR-derived amounts/dates may be uncertain — verify against original preview. "
            "Denies inaccessible documents without disclosing their names."
        ),
    )
    def fetch(
        id: str,
        offset: int = 0,
        chunkIndex: int | None = None,
        ctx: Context | None = None,
    ) -> str:
        """Fetch tool. Context is injected by MCPServer; keep it last so ChatGPT arg schemas stay simple."""
        if ctx is None:
            return json.dumps({"id": id, "denied": True, "text": None, "error": "MCP context unavailable."})
        factory = ctx.request_context.lifespan_context["session_factory"]
        cfg = ctx.request_context.lifespan_context["settings"]
        caller = _caller_user_id()
        try:
            with factory() as session:
                if caller is None:
                    _audit(session, tool="fetch", user_id=None, succeeded=False, error_category="unauthenticated")
                    session.commit()
                    return json.dumps({"id": id, "denied": True, "text": None, "error": "Unauthenticated."})
                channel = require_mcp_chat(session, caller)
                if not channel.allowed:
                    msg = channel_deny_message(channel.reason)
                    _audit(
                        session,
                        tool="fetch",
                        user_id=caller,
                        succeeded=False,
                        error_category="mcp_channel_denied",
                        detail=msg,
                    )
                    session.commit()
                    return json.dumps({"id": id, "denied": True, "text": None, "error": msg})
                try:
                    start = max(0, int(offset or 0))
                except (TypeError, ValueError):
                    start = 0
                try:
                    chunk_idx = int(chunkIndex) if chunkIndex is not None else None
                except (TypeError, ValueError):
                    chunk_idx = None
                result = docs.fetch_document(
                    session,
                    cfg,
                    caller,
                    id,
                    offset=start,
                    chunk_index=chunk_idx,
                )
                denied = bool(result.get("denied"))
                _audit(
                    session,
                    tool="fetch",
                    user_id=caller,
                    succeeded=not denied,
                    error_category="fetch_denied" if denied else None,
                    detail=result.get("error"),
                )
                session.commit()
                if denied:
                    return json.dumps(
                        {
                            "id": result.get("id"),
                            "title": None,
                            "text": None,
                            "url": None,
                            "relativePath": None,
                            "extension": None,
                            "sources": [],
                            "denied": True,
                            "error": result.get("error"),
                            "contentAvailable": None,
                            "indexingState": None,
                            "previewKind": None,
                            "contentUnavailableReason": None,
                            "ocrAvailable": None,
                        }
                    )
                return json.dumps(
                    {
                        "id": result.get("id"),
                        "title": result.get("title"),
                        "text": result.get("text"),
                        "url": result.get("url"),
                        "relativePath": result.get("relativePath"),
                        "extension": result.get("extension"),
                        "sources": [{"url": result.get("url"), "title": result.get("title")}],
                        "denied": False,
                        "error": result.get("error"),
                        "contentAvailable": result.get("contentAvailable"),
                        "indexingState": result.get("indexingState"),
                        "previewKind": result.get("previewKind"),
                        "previewNote": result.get("previewNote"),
                        "contentUnavailableReason": result.get("contentUnavailableReason"),
                        "ocrAvailable": result.get("ocrAvailable", False),
                        "ocrUsed": result.get("ocrUsed", False),
                        "ocrNote": result.get("ocrNote"),
                        "extractionMethod": result.get("extractionMethod"),
                        "extractorVersion": result.get("extractorVersion"),
                        "truncated": result.get("truncated"),
                        "incomplete": result.get("incomplete"),
                        "extractionNotes": result.get("extractionNotes"),
                        "offset": result.get("offset"),
                        "chunkIndex": result.get("chunkIndex"),
                        "totalChars": result.get("totalChars"),
                        "totalChunks": result.get("totalChunks"),
                        "hasMore": result.get("hasMore"),
                        "nextOffset": result.get("nextOffset"),
                        "nextChunkIndex": result.get("nextChunkIndex"),
                        "references": result.get("references") or [],
                        "untrustedContent": True,
                        "contentGuidance": result.get("contentGuidance"),
                    }
                )
        except Exception:
            log.exception("mcp.fetch.failed id=%s", id)
            # Return a tool-level error payload instead of letting the transport raise -32603.
            try:
                with factory() as session:
                    _audit(
                        session,
                        tool="fetch",
                        user_id=caller,
                        succeeded=False,
                        error_category="fetch_internal_error",
                        detail="Internal fetch failure.",
                    )
                    session.commit()
            except Exception:
                log.exception("mcp.fetch.audit_failed")
            return json.dumps(
                {
                    "id": id,
                    "denied": True,
                    "text": None,
                    "error": "Fetch failed due to an internal server error.",
                    "contentAvailable": None,
                    "indexingState": None,
                }
            )

    @mcp.tool(
        name="get_profile",
        description="Return the authenticated QKA user's safe profile. Never returns other employees' private information.",
    )
    def get_profile(ctx: Context) -> str:
        factory = ctx.request_context.lifespan_context["session_factory"]
        caller = _caller_user_id()
        with factory() as session:
            if caller is None:
                _audit(session, tool="get_profile", user_id=None, succeeded=False, error_category="unauthenticated")
                session.commit()
                return json.dumps({"denied": True, "error": "Unauthenticated."})
            access = evaluate_user(session, caller)
            if not access.allowed:
                _audit(session, tool="get_profile", user_id=caller, succeeded=False, error_category="access_denied")
                session.commit()
                return json.dumps({"denied": True, "error": "Access denied."})
            channel = require_mcp_chat(session, caller)
            if not channel.allowed:
                msg = channel_deny_message(channel.reason)
                _audit(session, tool="get_profile", user_id=caller, succeeded=False, error_category="mcp_channel_denied")
                session.commit()
                return json.dumps({"denied": True, "error": msg})
            user = session.execute(
                select(QkaUser)
                .options(selectinload(QkaUser.identities).selectinload(ExternalIdentity.google))
                .where(QkaUser.id == caller)
            ).scalar_one_or_none()
            email = None
            for identity in user.identities if user else []:
                if identity.primary_email:
                    email = identity.primary_email
                    break
            _audit(session, tool="get_profile", user_id=caller, succeeded=True, error_category=None)
            session.commit()
            return json.dumps(
                {
                    "id": str(caller),
                    "name": user.display_name if user else "QKA user",
                    "nickname": email or str(caller),
                    "superAdmin": bool(user.is_super_admin) if user else False,
                }
            )

    def _gate(session: Session, tool: str, caller: uuid.UUID | None) -> str | None:
        if caller is None:
            _audit(session, tool=tool, user_id=None, succeeded=False, error_category="unauthenticated")
            return "Unauthenticated."
        channel = require_mcp_chat(session, caller)
        if not channel.allowed:
            msg = channel_deny_message(channel.reason)
            _audit(session, tool=tool, user_id=caller, succeeded=False, error_category="mcp_channel_denied", detail=msg)
            return msg
        return None

    @mcp.tool(
        name="get_document_download_link",
        description=(
            "Return a secure QKA browser handoff link for the original Synology file. "
            "Requires Download Originals. Does not return file bytes or SMB paths. "
            "The employee must open the link while signed into QKA."
        ),
    )
    def get_document_download_link(document_id: str, ctx: Context) -> str:
        factory = ctx.request_context.lifespan_context["session_factory"]
        cfg = ctx.request_context.lifespan_context["settings"]
        caller = _caller_user_id()
        with factory() as session:
            deny = _gate(session, "get_document_download_link", caller)
            if deny:
                session.commit()
                return json.dumps({"denied": True, "error": deny, "downloadLink": None})
            result = caps.get_document_download_link(session, cfg, caller, document_id)  # type: ignore[arg-type]
            _audit(
                session,
                tool="get_document_download_link",
                user_id=caller,
                succeeded=not result.get("denied"),
                error_category="download_denied" if result.get("denied") else None,
                detail=result.get("error"),
            )
            session.commit()
            return json.dumps(result)

    @mcp.tool(
        name="get_document_preview",
        description=(
            "Return preview metadata and a secure QKA viewer handoff link. "
            "Requires View Documents. Does not embed original file bytes. "
            "Reports the actual supported preview kind (not LibreOffice/COM rendering)."
        ),
    )
    def get_document_preview(document_id: str, ctx: Context) -> str:
        factory = ctx.request_context.lifespan_context["session_factory"]
        cfg = ctx.request_context.lifespan_context["settings"]
        caller = _caller_user_id()
        with factory() as session:
            deny = _gate(session, "get_document_preview", caller)
            if deny:
                session.commit()
                return json.dumps({"denied": True, "error": deny, "previewLink": None})
            result = caps.get_document_preview(session, cfg, caller, document_id)  # type: ignore[arg-type]
            _audit(
                session,
                tool="get_document_preview",
                user_id=caller,
                succeeded=not result.get("denied"),
                error_category="preview_denied" if result.get("denied") else None,
                detail=result.get("error"),
            )
            session.commit()
            return json.dumps(result)

    @mcp.tool(
        name="list_folders",
        description=(
            "List authorized Synology folders for the bound employee. "
            "Uses PostgreSQL inventory (no Synology round-trip). Paginated."
        ),
    )
    def list_folders(
        source_id: str | None = None,
        parent_folder_id: str | None = None,
        parent_path: str | None = None,
        page_size: int = 25,
        cursor: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        if ctx is None:
            return json.dumps({"denied": True, "error": "MCP context unavailable.", "folders": []})
        factory = ctx.request_context.lifespan_context["session_factory"]
        cfg = ctx.request_context.lifespan_context["settings"]
        caller = _caller_user_id()
        with factory() as session:
            deny = _gate(session, "list_folders", caller)
            if deny:
                session.commit()
                return json.dumps({"denied": True, "error": deny, "folders": []})
            result = caps.list_folders(
                session,
                cfg,
                caller,  # type: ignore[arg-type]
                source_id=source_id,
                parent_folder_id=parent_folder_id,
                parent_path=parent_path,
                page_size=page_size,
                cursor=cursor,
            )
            _audit(
                session,
                tool="list_folders",
                user_id=caller,
                succeeded=not result.get("denied"),
                error_category="list_folders_denied" if result.get("denied") else None,
                detail=result.get("error"),
            )
            session.commit()
            return json.dumps(result)

    @mcp.tool(
        name="list_documents",
        description=(
            "List authorized Synology documents from inventory (including unindexed filenames). "
            "Supports folder, file_type, filename_query filters. Paginated; never returns the full inventory."
        ),
    )
    def list_documents(
        source_id: str | None = None,
        folder_id: str | None = None,
        folder_path: str | None = None,
        recursive: bool = False,
        file_type: str | None = None,
        filename_query: str | None = None,
        page_size: int = 25,
        cursor: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        if ctx is None:
            return json.dumps({"denied": True, "error": "MCP context unavailable.", "documents": []})
        factory = ctx.request_context.lifespan_context["session_factory"]
        cfg = ctx.request_context.lifespan_context["settings"]
        caller = _caller_user_id()
        with factory() as session:
            deny = _gate(session, "list_documents", caller)
            if deny:
                session.commit()
                return json.dumps({"denied": True, "error": deny, "documents": []})
            result = caps.list_documents(
                session,
                cfg,
                caller,  # type: ignore[arg-type]
                source_id=source_id,
                folder_id=folder_id,
                folder_path=folder_path,
                recursive=bool(recursive),
                file_type=file_type,
                filename_query=filename_query,
                page_size=page_size,
                cursor=cursor,
            )
            _audit(
                session,
                tool="list_documents",
                user_id=caller,
                succeeded=not result.get("denied"),
                error_category="list_documents_denied" if result.get("denied") else None,
                detail=result.get("error"),
            )
            session.commit()
            return json.dumps(result)

    @mcp.tool(
        name="get_index_status",
        description=(
            "Read-only indexing/sync progress from PostgreSQL job counters. "
            "Super Admin sees full details; other employees see a limited summary for accessible sources."
        ),
    )
    def get_index_status(
        source_id: str | None = None,
        job_id: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        if ctx is None:
            return json.dumps({"denied": True, "error": "MCP context unavailable."})
        factory = ctx.request_context.lifespan_context["session_factory"]
        cfg = ctx.request_context.lifespan_context["settings"]
        caller = _caller_user_id()
        with factory() as session:
            deny = _gate(session, "get_index_status", caller)
            if deny:
                session.commit()
                return json.dumps({"denied": True, "error": deny})
            result = caps.get_index_status(
                session, cfg, caller, source_id=source_id, job_id=job_id  # type: ignore[arg-type]
            )
            _audit(
                session,
                tool="get_index_status",
                user_id=caller,
                succeeded=not result.get("denied"),
                error_category="index_status_denied" if result.get("denied") else None,
                detail=result.get("error"),
            )
            session.commit()
            return json.dumps(result)

    @mcp.tool(
        name="start_indexing",
        description=(
            "Super Admin only. Start an existing Synology indexing operation after a short-lived "
            "confirmation created in the QKA Synology admin UI (confirmation_id required). "
            "Operations: sync, resync, extract_missing, retry_failed, reindex_selected. "
            "Does not start jobs from ChatGPT browse/search alone."
        ),
    )
    def start_indexing(
        confirmation_id: str | None = None,
        operation: str | None = None,
        source_id: str | None = None,
        document_ids: list[str] | None = None,
        ctx: Context | None = None,
    ) -> str:
        if ctx is None:
            return json.dumps({"accepted": False, "denied": True, "error": "MCP context unavailable."})
        factory = ctx.request_context.lifespan_context["session_factory"]
        cfg = ctx.request_context.lifespan_context["settings"]
        caller = _caller_user_id()
        with factory() as session:
            deny = _gate(session, "start_indexing", caller)
            if deny:
                session.commit()
                return json.dumps({"accepted": False, "denied": True, "error": deny})
            result = caps.start_indexing(
                session,
                cfg,
                caller,  # type: ignore[arg-type]
                confirmation_id=confirmation_id,
                operation=operation,
                source_id=source_id,
                document_ids=document_ids,
            )
            _audit(
                session,
                tool="start_indexing",
                user_id=caller,
                succeeded=bool(result.get("accepted")),
                error_category=None if result.get("accepted") else "start_indexing_denied",
                detail=result.get("error") or result.get("operation"),
            )
            session.commit()
            return json.dumps(result)

    return mcp
