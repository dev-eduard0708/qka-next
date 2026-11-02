from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "qka-next-api"}


@router.get("/ready")
def ready(request: Request) -> dict:
    factory = request.app.state.session_factory
    with factory() as session:
        session.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}
