from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from qka_api.auth import require_csrf, require_principal
from qka_api.deps import get_session, get_settings_dep
from qka_api.mcp_oauth import McpOAuthService
from qka_api.services import mcp_diagnostics as diag
from qka_shared.allowlist import load_active_user
from qka_shared.config import Settings
from qka_shared.models import QkaUser

router = APIRouter(prefix="/api/v1/admin/integrations")


def _require_super_admin(
    request: Request,
    settings: Settings,
    session: Session,
    *,
    mutating: bool,
) -> QkaUser:
    if mutating:
        require_csrf(request)
    principal = require_principal(request, settings)
    user = load_active_user(session, principal.user_id)
    if user is None or not user.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super Admin access is required.")
    return user


def _oauth(request: Request) -> McpOAuthService:
    oauth = getattr(request.app.state, "mcp_oauth", None)
    if oauth is None:
        raise HTTPException(status_code=503, detail="MCP OAuth service is unavailable.")
    return oauth


@router.get("/chatgpt/status")
def chatgpt_status(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    return diag.collect_status(session, settings, _oauth(request))


@router.get("/chatgpt/diagnostics")
def chatgpt_diagnostics(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=False)
    status_payload = diag.collect_status(session, settings, _oauth(request))
    return {
        "status": status_payload,
        "oauthEnabled": _oauth(request).is_enabled,
        "resource": settings.mcp_resource,
        "issuer": settings.mcp_issuer,
        "tunnelReadyUrl": settings.mcp_tunnel_ready_url,
        "legacyApiUnchanged": True,
        "legacyMcpPort": 5255,
        "qkaNextMcpPort": settings.api_port,
    }


@router.post("/chatgpt/test")
def chatgpt_test(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=403, detail="The database is read-only.")
    return diag.run_connection_test(session, settings, _oauth(request), actor.id)
