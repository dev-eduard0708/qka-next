from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from qka_api.auth import require_principal
from qka_api.deps import get_session, get_settings_dep
from qka_api.services.document_preview import preview_document, preview_media
from qka_api.services.documents import (
    authorize_download,
    fetch_document,
    list_inventory,
    search_catalog,
    search_documents,
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
    q: str = Query(min_length=1, max_length=200),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    principal = require_principal(request, settings)
    hits, error = search_documents(session, settings, principal.user_id, q)
    return {
        "results": [
            {
                "id": hit.id,
                "title": hit.title,
                "url": hit.url,
                "snippet": hit.snippet,
                "relativePath": hit.relative_path,
                "extension": hit.extension,
                "fileType": hit.file_type,
                "sizeBytes": hit.size_bytes,
            }
            for hit in hits
        ],
        "error": error,
    }


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


@router.get("/documents/{document_id}/preview")
def document_preview(
    document_id: str,
    request: Request,
    sheet: int = Query(default=0, ge=0, le=200),
    page: int = Query(default=1, ge=1, le=5000),
    slide: int = Query(default=0, ge=0, le=5000),
    rowOffset: int = Query(default=0, ge=0, le=1_000_000),
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


@router.get("/documents/{document_id}")
def document_detail(
    document_id: str,
    request: Request,
    offset: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    principal = require_principal(request, settings)
    return fetch_document(session, settings, principal.user_id, document_id, offset)


@router.get("/documents/{document_id}/download")
def document_download(
    document_id: str,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    """Authorize original-file download (separate from View / indexed-text preview)."""
    principal = require_principal(request, settings)
    return authorize_download(session, settings, principal.user_id, document_id)
