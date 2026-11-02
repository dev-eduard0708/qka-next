from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from qka_api.google_oidc import GoogleOidcService
from qka_shared.config import Settings, get_settings


def get_settings_dep() -> Settings:
    return get_settings()


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    with factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_google_oidc(request: Request) -> GoogleOidcService:
    return request.app.state.google_oidc
