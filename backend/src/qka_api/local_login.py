from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from qka_shared.config import Settings
from qka_shared.models import LocalLogin, QkaUser

log = logging.getLogger("qka.local_login")
PBKDF2_ROUNDS = 200_000
STABLE_SUPER_ID = uuid.UUID("a1111111-0000-4000-8000-000000000001")
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS qka_next_local_login (
  id UUID PRIMARY KEY,
  username VARCHAR(64) NOT NULL UNIQUE,
  password_hash VARCHAR(256) NOT NULL,
  qka_user_id UUID NOT NULL REFERENCES qka_user("Id"),
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
)
"""


def hash_password(password: str, salt: bytes | None = None) -> str:
    raw = (password or "").encode("utf-8")
    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw, salt_bytes, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt_bytes.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds_s, salt_hex, digest_hex = (stored or "").split("$", 3)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    try:
        rounds = int(rounds_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, rounds)
    return hmac.compare_digest(actual, expected)


def _targets_qka_next(database_url: str) -> bool:
    return "qka_next" in (database_url or "").lower()


def bootstrap_local_super_admin(session: Session, settings: Settings) -> None:
    if settings.database_read_only or not _targets_qka_next(settings.database_url):
        return
    session.execute(text(CREATE_TABLE))
    session.flush()
    username = (settings.local_admin_username or "super").strip().lower() or "super"
    existing = session.execute(select(LocalLogin).where(LocalLogin.username == username)).scalar_one_or_none()
    if existing is not None:
        return
    password = (settings.local_admin_password or "").strip()
    if not password:
        if settings.is_production:
            log.warning("local_admin.skipped reason=no_password")
            return
        password = "123"
    if settings.is_production and password == "123":
        log.error("local_admin.refused reason=default_password_in_production")
        return
    now = datetime.now(timezone.utc)
    user = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(True))).scalars().first()
    if user is None:
        user = session.get(QkaUser, STABLE_SUPER_ID)
    if user is None:
        user = QkaUser(
            id=STABLE_SUPER_ID,
            display_name="Super Admin",
            status=0,
            login_enabled=True,
            is_directory_operator=True,
            is_super_admin=True,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.flush()
    elif not user.is_super_admin or not user.login_enabled:
        user.is_super_admin = True
        user.login_enabled = True
        user.status = 0
        user.updated_at = now
        session.flush()
    session.add(
        LocalLogin(
            id=uuid.uuid4(),
            username=username,
            password_hash=hash_password(password),
            qka_user_id=user.id,
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()
    log.info("local_admin.ready username=%s", username)


def authenticate_local(session: Session, username: str, password: str) -> QkaUser | None:
    normalized = (username or "").strip().lower()
    if not normalized or not password:
        return None
    row = session.execute(select(LocalLogin).where(LocalLogin.username == normalized)).scalar_one_or_none()
    if row is None or not verify_password(password, row.password_hash):
        return None
    user = session.get(QkaUser, row.qka_user_id)
    if user is None or user.status != 0 or not user.login_enabled:
        return None
    return user
