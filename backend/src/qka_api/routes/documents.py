from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from qka_api.auth import require_principal
from qka_api.deps import get_session, get_settings_dep
from qka_api.services.document_preview import (
    preview_document,
    preview_engine_status,
    preview_media,
    preview_page_image,
)
from qka_api.services.desktop_open import mint_desktop_open, redeem_desktop_open
from qka_api.services.documents import (
    authorize_download,
    browse_workspace,
    diagnose_document_access,
    fetch_document,
    list_inventory,
    read_original_bytes,
    search_catalog,
    summary,
)
from qka_api.smb import SmbGateway, default_gateway
from qka_shared.config import Settings

router = APIRouter(prefix="/api/v1")


def _gateway(request: Request) -> SmbGateway:
    return getattr(request.app.state, "smb_gateway", None) or default_gateway()


@router.get("/diagnostics/summary")
def diagnostics_summary(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    require_principal(request, settings)
    return summary(session, settings)


@router.get("/search/catalog")
def search_sources_catalog(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    principal = require_principal(request, settings)
    return search_catalog(session, settings, principal.user_id)


@router.get("/search")
def search(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    fileType: str | None = Query(default=None, max_length=32),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default="name", max_length=32),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    """Workspace browse/search. MCP keeps using search_documents() directly."""
    principal = require_principal(request, settings)
    # Preserve the historical content-only path when callers pass only q with defaults
    # and no browse params — still fine to use browse_workspace for all UI/API cases.
    return browse_workspace(
        session,
        settings,
        principal.user_id,
        query=q,
        file_type=fileType,
        page=page,
        page_size=pageSize,
        sort=sort,
    )


@router.get("/documents")
def documents(
    request: Request,
    q: str | None = None,
    extension: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    principal = require_principal(request, settings)
    items, total, error = list_inventory(
        session, settings, principal.user_id, q, extension, limit, offset
    )
    return {"items": items, "total": total, "error": error}


@router.get("/diagnostics/preview-engine")
def diagnostics_preview_engine(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    require_principal(request, settings)
    return preview_engine_status(settings)


@router.get("/diagnostics/document-access")
def diagnostics_document_access(
    request: Request,
    documentId: str = Query(..., min_length=8, max_length=64),
    forUserId: str | None = Query(default=None, max_length=64),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    """Super Admin: inventory presence, extraction state, and access decision for one document."""
    import uuid as _uuid

    principal = require_principal(request, settings)
    subject: _uuid.UUID | None = None
    if forUserId:
        try:
            subject = _uuid.UUID(forUserId) if "-" in forUserId else _uuid.UUID(hex=forUserId)
        except ValueError:
            return {"ok": False, "error": "invalid_for_user_id"}
    return diagnose_document_access(
        session,
        settings,
        principal.user_id,
        documentId,
        for_user_id=subject,
    )


@router.get("/documents/{document_id}/preview")
def document_preview(
    document_id: str,
    request: Request,
    sheet: int = Query(default=0, ge=0, le=200),
    page: int = Query(default=1, ge=1, le=5000),
    slide: int = Query(default=0, ge=0, le=5000),
    rowOffset: int = Query(default=0, ge=0, le=1_000_000),
    view: str = Query(default="original", max_length=32),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    principal = require_principal(request, settings)
    return preview_document(
        session,
        settings,
        principal.user_id,
        document_id,
        _gateway(request),
        sheet=sheet,
        page=page,
        slide=slide,
        row_offset=rowOffset,
        view_mode=view,
    )


@router.get("/documents/{document_id}/preview/media")
def document_preview_media(
    document_id: str,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> Response:
    principal = require_principal(request, settings)
    payload, media_type, error = preview_media(
        session, settings, principal.user_id, document_id, _gateway(request)
    )
    if payload is None:
        return Response(
            content=json.dumps(error),
            status_code=403 if error.get("denied") else 404,
            media_type="application/json",
        )
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline; filename=\"preview.jpg\"",
        },
    )


@router.get("/documents/{document_id}/preview/pages/{page}")
def document_preview_page(
    document_id: str,
    page: int,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> Response:
    """Authorized JPEG for one original-format rendered page (not the original file)."""
    principal = require_principal(request, settings)
    if page < 1 or page > 5000:
        return Response(
            content=json.dumps({"denied": True, "error": "Invalid page."}),
            status_code=400,
            media_type="application/json",
        )
    payload, media_type, error = preview_page_image(
        session, settings, principal.user_id, document_id, page, _gateway(request)
    )
    if payload is None:
        return Response(
            content=json.dumps(error),
            status_code=403 if error.get("denied") else 404,
            media_type="application/json",
        )
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="page-{page:04d}.jpg"',
        },
    )


@router.get("/documents/{document_id}")
def document_detail(
    document_id: str,
    request: Request,
    offset: int = Query(default=0, ge=0),
    chunkIndex: int | None = Query(default=None, ge=0),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    principal = require_principal(request, settings)
    return fetch_document(
        session,
        settings,
        principal.user_id,
        document_id,
        offset,
        chunk_index=chunkIndex,
    )


@router.get("/documents/{document_id}/download")
def document_download(
    document_id: str,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> Response:
    """Stream original Synology bytes after rechecking Download Originals permission.

    intent=download (default) → Content-Disposition: attachment
    intent=open → Content-Disposition: inline so Windows/browser can use registered apps
    Never exposes SMB paths or Synology credentials. Never auto-transfers without a user action.
    """
    principal = require_principal(request, settings)
    # Metadata-only probe used by older clients / unit tests when Accept prefers JSON
    # and no SMB content is requested via download=1. Prefer streaming by default.
    prefer_meta = (request.query_params.get("meta") or "").lower() in {"1", "true", "yes"}
    if prefer_meta:
        return JSONResponse(authorize_download(session, settings, principal.user_id, document_id))

    payload, meta = read_original_bytes(
        session, settings, principal.user_id, document_id, _gateway(request)
    )
    if payload is None:
        status = 403 if meta.get("requiresDownloadGrant") or meta.get("denied") else 404
        return JSONResponse(meta, status_code=status)

    file_name = meta.get("fileName") or "document"
    ascii_name = file_name.encode("ascii", "ignore").decode("ascii") or "document"
    intent = (request.query_params.get("intent") or "download").strip().lower()
    disposition_kind = "inline" if intent == "open" else "attachment"
    disposition = f"{disposition_kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name)}"
    return Response(
        content=payload,
        media_type=meta.get("mediaType") or "application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": disposition,
            "X-QKA-Recommended-App": meta.get("recommendedApp") or "",
            "X-QKA-Open-With-Label": meta.get("openWithLabel") or "",
        },
    )


def _desktop_open_base(request: Request, settings: Settings) -> str:
    """Absolute origin for Office URI / browser open fetches.

    In development, point desktop apps at the FastAPI bind address so they do not
    depend on the Vite proxy. In production, use the public request host.
    """
    if getattr(settings, "is_production", False):
        forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
        if host:
            scheme = forwarded or request.url.scheme or "https"
            return f"{scheme}://{host}"
    host = settings.api_host if settings.api_host not in {"0.0.0.0", "::", ""} else "127.0.0.1"
    return f"http://{host}:{settings.api_port}"


def _is_loopback_client(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    if host in {"127.0.0.1", "::1", "localhost"}:
        return True
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded in {"127.0.0.1", "::1", "localhost"}


@router.post("/documents/{document_id}/desktop-open")
def document_desktop_open(
    document_id: str,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> JSONResponse:
    """Open original with the Windows default app (local) or mint a remote open URL."""
    principal = require_principal(request, settings)
    body = mint_desktop_open(
        session,
        settings,
        principal.user_id,
        document_id,
        public_base_url=_desktop_open_base(request, settings),
        gateway=_gateway(request),
        prefer_local_launch=_is_loopback_client(request),
    )
    status = 200 if body.get("ok") else (403 if body.get("denied") else 400)
    return JSONResponse(body, status_code=status)


def _desktop_open_response(payload: bytes, meta: dict) -> Response:
    file_name = meta.get("fileName") or "document"
    ascii_name = file_name.encode("ascii", "ignore").decode("ascii") or "document"
    disposition = f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name)}"
    return Response(
        content=payload,
        media_type=meta.get("mediaType") or "application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": disposition,
            "X-QKA-Recommended-App": meta.get("recommendedApp") or "",
            "X-QKA-Open-With-Label": meta.get("openWithLabel") or "",
        },
    )


@router.get("/desktop-open/{token}/{file_name}")
@router.get("/desktop-open/{token}")
def desktop_open_redeem(
    token: str,
    request: Request,
    file_name: str | None = None,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> Response:
    """Stream original bytes for a desktop-open ticket (remote / protocol fallback)."""
    _ = file_name
    payload, meta = redeem_desktop_open(session, settings, token, _gateway(request))
    if payload is None:
        status = 403 if meta.get("requiresDownloadGrant") or meta.get("denied") else 404
        return JSONResponse(meta, status_code=status)
    return _desktop_open_response(payload, meta)
