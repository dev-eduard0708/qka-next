from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from qka_api.auth import require_csrf, require_principal
from qka_api.deps import get_session, get_settings_dep
from qka_shared.allowlist import load_active_user
from qka_shared.config import Settings
from qka_shared.email_address import try_normalize
from qka_shared.models import AllowedEmail, QkaUser

router = APIRouter(prefix="/api/v1/access")


class AddEmailRequest(BaseModel):
    email: str


class SetEnabledRequest(BaseModel):
    enabled: bool


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


def _to_dto(row: AllowedEmail) -> dict:
    return {
        "id": str(row.id),
        "email": row.email,
        "enabled": row.enabled,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/emails")
def list_emails(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> list[dict]:
    _require_super_admin(request, settings, session, mutating=False)
    rows = session.execute(select(AllowedEmail).order_by(AllowedEmail.email_normalized)).scalars().all()
    return [_to_dto(row) for row in rows]


@router.post("/emails")
def add_email(
    body: AddEmailRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    email, error = try_normalize(body.email)
    if email is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    exists = session.execute(select(AllowedEmail).where(AllowedEmail.email_normalized == email)).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That email is already on the allowlist.")
    now = datetime.now(timezone.utc)
    row = AllowedEmail(
        id=uuid.uuid4(),
        email=email,
        email_normalized=email,
        enabled=True,
        created_at=now,
        updated_at=now,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    session.add(row)
    session.flush()
    return _to_dto(row)


@router.post("/emails/{email_id}/enabled")
def set_enabled(
    email_id: uuid.UUID,
    body: SetEnabledRequest,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    actor = _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    row = session.get(AllowedEmail, email_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allowlist entry was not found.")
    row.enabled = body.enabled
    row.updated_at = datetime.now(timezone.utc)
    row.updated_by_user_id = actor.id
    session.flush()
    return _to_dto(row)


@router.post("/emails/{email_id}/remove")
def remove_email(
    email_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> dict:
    _require_super_admin(request, settings, session, mutating=True)
    if settings.database_read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The database is read-only.")
    row = session.get(AllowedEmail, email_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allowlist entry was not found.")
    session.delete(row)
    session.flush()
    return {"ok": True}
