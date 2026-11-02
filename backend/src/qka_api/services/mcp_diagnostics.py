"""Super Admin diagnostics for MCP / ChatGPT staging status (no secrets)."""

from __future__ import annotations

import socket
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from qka_api.mcp_oauth import McpOAuthService
from qka_api.services import ai_chat, documents as docs
from qka_shared.config import Settings
from qka_shared.models import (
    AllowedEmail,
    ExternalIdentity,
    McpToolAudit,
    SynologyConnectionSettings,
    SynologyDocumentContent,
    SynologyFileInventory,
    SynologySyncJob,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tcp_ok(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _status(ok: bool, *, configured: bool = True, error: str | None = None) -> dict:
    if not configured:
        return {"status": "not_configured", "error": error}
    if error:
        return {"status": "error", "error": error}
    return {"status": "connected" if ok else "disconnected", "error": None}


def collect_status(session: Session, settings: Settings, oauth: McpOAuthService) -> dict:
    checked_at = _now_iso()
    api = _status(True)
    api["lastCheck"] = checked_at

    try:
        session.execute(select(1))
        postgres = _status(True)
    except Exception as exc:  # noqa: BLE001
        postgres = _status(False, error=str(exc)[:200])
    postgres["lastCheck"] = checked_at

    synology_count = session.scalar(
        select(func.count()).select_from(SynologyConnectionSettings).where(
            SynologyConnectionSettings.configured_shared_folder_path != ""
        )
    ) or 0
    synology = _status(synology_count > 0, configured=True)
    synology["connectionCount"] = int(synology_count)
    synology["lastCheck"] = checked_at

    last_job = session.execute(select(SynologySyncJob).order_by(desc(SynologySyncJob.created_at)).limit(1)).scalar_one_or_none()
    sync = {
        "status": "not_configured" if last_job is None else ("connected" if last_job.status == 2 else "error" if last_job.status == 3 else "disconnected"),
        "lastJobStatus": None if last_job is None else last_job.status,
        "lastSuccessfulSyncAt": last_job.completed_at.isoformat() if last_job and last_job.status == 2 and last_job.completed_at else None,
        "error": last_job.safe_error if last_job and last_job.status == 3 else None,
        "lastCheck": checked_at,
    }

    inventory = session.scalar(select(func.count()).select_from(SynologyFileInventory)) or 0
    extracted = session.scalar(
        select(func.count()).select_from(SynologyDocumentContent).where(SynologyDocumentContent.status == 1)
    ) or 0
    index = _status(extracted > 0, configured=inventory > 0 or extracted > 0)
    index["inventoryCount"] = int(inventory)
    index["extractedCount"] = int(extracted)
    index["lastCheck"] = checked_at

    google_configured = bool(settings.google_client_id.strip() and settings.google_client_secret.strip())
    allowlisted = session.scalar(select(func.count()).select_from(AllowedEmail).where(AllowedEmail.enabled.is_(True))) or 0
    google_users = session.scalar(
        select(func.count()).select_from(ExternalIdentity).where(
            ExternalIdentity.provider == 1,
            ExternalIdentity.link_status == 2,
            ExternalIdentity.account_status == 0,
        )
    ) or 0
    google = _status(google_configured and allowlisted > 0, configured=google_configured)
    google["allowlistedEmails"] = int(allowlisted)
    google["linkedUsers"] = int(google_users)
    google["lastCheck"] = checked_at

    mcp_endpoint = settings.mcp_resource
    mcp_auth_ready = oauth.is_enabled
    mcp_server = _status(True, configured=True)
    mcp_server.update(
        {
            "endpoint": mcp_endpoint,
            "protocol": "streamable-http",
            "authReady": mcp_auth_ready,
            "authStatus": "connected" if mcp_auth_ready else "not_configured",
            "tools": ["search", "fetch", "get_profile"],
            "lastCheck": checked_at,
            "oauthStats": oauth.stats,
        }
    )

    success = session.scalar(
        select(func.count()).select_from(McpToolAudit).where(McpToolAudit.succeeded.is_(True))
    ) or 0
    failed = session.scalar(
        select(func.count()).select_from(McpToolAudit).where(McpToolAudit.succeeded.is_(False))
    ) or 0
    last_auth = session.execute(
        select(McpToolAudit).where(McpToolAudit.succeeded.is_(True)).order_by(desc(McpToolAudit.created_at)).limit(1)
    ).scalar_one_or_none()
    tools = {
        "status": "connected" if success > 0 else ("error" if failed > 0 else "not_verified"),
        "available": ["search", "fetch", "get_profile"],
        "successCount": int(success),
        "failureCount": int(failed),
        "lastAuthenticatedAt": last_auth.created_at.isoformat() if last_auth else None,
        "lastTool": last_auth.tool_name if last_auth else None,
        "lastCheck": checked_at,
    }

    tunnel = _probe_tunnel(settings)
    tunnel["lastCheck"] = checked_at

    # Live Verified only from ChatGPT-originated MCP audit evidence — never from env timestamps
    # or local inspector/admin tests.
    chatgpt_audit = session.execute(
        select(McpToolAudit)
        .where(
            McpToolAudit.succeeded.is_(True),
            McpToolAudit.detail.ilike("client=chatgpt%"),
            McpToolAudit.tool_name.in_(("search", "fetch", "get_profile")),
        )
        .order_by(desc(McpToolAudit.created_at))
        .limit(1)
    ).scalar_one_or_none()
    live_at = chatgpt_audit.created_at.isoformat() if chatgpt_audit else None
    chatgpt = {
        "status": "live_verified" if live_at else "not_verified",
        "pluginName": settings.mcp_chatgpt_plugin_name,
        "environment": settings.mcp_chatgpt_environment,
        "workspaceAssociation": "not_verifiable_from_api",
        "lastLiveVerifiedAt": live_at,
        "lastLiveTool": chatgpt_audit.tool_name if chatgpt_audit else None,
        "connectionState": "live_verified" if live_at else "not_verified",
        "testInstructions": (
            "ChatGPT Business admin must add plugin "
            f"'{settings.mcp_chatgpt_plugin_name}' on the staging tunnel, complete Google OAuth, "
            "and run search/fetch. Dashboard Live Verified appears only after a matching "
            "client=chatgpt audit row — not from local Test MCP or manual timestamps."
        ),
        "lastCheck": checked_at,
        "error": None if live_at else "No confirmed ChatGPT Business invocation recorded in MCP audit.",
    }

    openai = ai_chat.openai_status(session, settings)
    openai_error = None
    if not openai["configured"]:
        openai_error = openai["message"]
    elif openai["lastError"] and not openai["lastSuccessfulRequestAt"]:
        openai_error = openai["lastError"]
    openai_block = {
        "status": openai["status"],
        "configured": openai["configured"],
        "available": openai["available"],
        "provider": openai.get("provider"),
        "connectionLabel": openai.get("connectionLabel"),
        "model": openai["model"],
        "modelAvailable": openai.get("modelAvailable"),
        "creditStatus": openai.get("creditStatus"),
        "creditMessage": openai.get("creditMessage"),
        "billingUrl": openai.get("billingUrl"),
        "dataSharingPermitted": openai["dataSharingPermitted"],
        "successCount": openai["successCount"],
        "errorCount": openai["errorCount"],
        "lastSuccessfulRequestAt": openai["lastSuccessfulRequestAt"],
        "lastErrorAt": openai["lastErrorAt"],
        "lastError": openai["lastError"],
        "error": openai_error or (openai.get("creditMessage") if openai.get("creditStatus") == "exhausted" else None),
        "lastCheck": checked_at,
    }

    return {
        "checkedAt": checked_at,
        "fastapi": api,
        "postgresql": postgres,
        "synologyNas": synology,
        "synologySync": sync,
        "documentIndex": index,
        "googleWorkspaceAuth": google,
        "openaiApi": openai_block,
        "mcpServer": mcp_server,
        "mcpTools": tools,
        "secureMcpTunnel": tunnel,
        "chatgptBusiness": chatgpt,
    }


def _probe_tunnel(settings: Settings) -> dict:
    url = (settings.mcp_tunnel_ready_url or "").strip()
    if not url:
        return _status(False, configured=False, error="Tunnel ready URL not configured.")
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not _tcp_ok(host, port):
        return _status(False, configured=True, error=f"Tunnel health port {host}:{port} not reachable.")
    try:
        response = httpx.get(url, timeout=2.0)
        if response.status_code >= 400:
            return _status(False, configured=True, error=f"Tunnel readyz HTTP {response.status_code}")
        return _status(True, configured=True)
    except Exception as exc:  # noqa: BLE001
        return _status(False, configured=True, error=str(exc)[:200])


def run_connection_test(
    session: Session,
    settings: Settings,
    oauth: McpOAuthService,
    actor_id: uuid.UUID,
) -> dict:
    """Safe Super Admin MCP connection test. Does not bypass OAuth/ACL."""
    checked_at = _now_iso()
    steps: list[dict] = []

    steps.append({"name": "oauth_metadata", "ok": oauth.is_enabled, "detail": None if oauth.is_enabled else "MCP OAuth disabled"})
    steps.append(
        {
            "name": "tools_declared",
            "ok": True,
            "detail": "search,fetch,get_profile",
        }
    )

    inspector_ok = bool(settings.mcp_inspector_bearer.strip() and settings.mcp_inspector_user_id)
    steps.append(
        {
            "name": "inspector_bearer_configured",
            "ok": inspector_ok,
            "detail": None if inspector_ok else "Set QKA_MCP_INSPECTOR_BEARER and QKA_MCP_INSPECTOR_USER_ID for local authenticated tests.",
        }
    )

    search_ok = False
    fetch_ok = False
    deny_ok = False
    if inspector_ok and settings.mcp_inspector_user_id is not None:
        token = oauth.validate_access_token(settings.mcp_inspector_bearer)
        steps.append({"name": "token_validate", "ok": token is not None, "detail": None if token else "Inspector bearer rejected"})
        if token is not None:
            hits, err = docs.search_documents(session, settings, settings.mcp_inspector_user_id, "Laptop")
            search_ok = err is None
            steps.append(
                {
                    "name": "authorized_search",
                    "ok": search_ok,
                    "detail": err or f"hits={len(hits)}",
                }
            )
            if hits:
                fetched = docs.fetch_document(session, settings, settings.mcp_inspector_user_id, hits[0].id)
                fetch_ok = not fetched.get("denied")
                steps.append({"name": "authorized_fetch", "ok": fetch_ok, "detail": fetched.get("error")})
            else:
                steps.append({"name": "authorized_fetch", "ok": False, "detail": "No authorized Laptop hits for inspector user."})
            denied = docs.fetch_document(session, settings, settings.mcp_inspector_user_id, "00000000000000000000000000000000")
            deny_ok = bool(denied.get("denied"))
            steps.append({"name": "unauthorized_fetch_denied", "ok": deny_ok, "detail": denied.get("error")})
            session.add(
                McpToolAudit(
                    id=uuid.uuid4(),
                    tool_name="admin_test",
                    qka_user_id=actor_id,
                    succeeded=search_ok and deny_ok,
                    error_category=None if (search_ok and deny_ok) else "admin_test",
                    detail="Super Admin Test MCP Connection",
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.flush()

    tunnel = _probe_tunnel(settings)
    steps.append({"name": "tunnel_ready", "ok": tunnel.get("status") == "connected", "detail": tunnel.get("error")})

    overall = all(step["ok"] for step in steps if step["name"] in {"oauth_metadata", "tools_declared"})
    return {
        "checkedAt": checked_at,
        "overallOk": overall and (not inspector_ok or (search_ok and deny_ok)),
        "steps": steps,
        "chatgptLiveVerified": False,
        "note": "Local MCP handshake/auth tests do not set ChatGPT Live Verified. Only client=chatgpt audit evidence does.",
    }
