"""QKA-Next MCP Streamable HTTP tools (search, fetch, get_profile)."""

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
            "Read-only Synology document tools for QKA-Next. "
            "search/fetch always apply the authenticated employee's ACL. "
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

    return mcp
