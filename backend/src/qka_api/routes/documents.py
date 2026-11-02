from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from qka_api.auth import require_principal
from qka_api.deps import get_session, get_settings_dep
from qka_api.services.documents import (
    authorize_download,
    fetch_document,
    list_inventory,
    search_documents,
    summary,
)
from qka_shared.config import Settings

router = APIRouter(prefix="/api/v1")


@router.get("/diagnostics/summary")
def diagnostics_summary(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    require_principal(request, settings)
    return summary(session, settings)


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
