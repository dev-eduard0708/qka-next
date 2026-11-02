from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from qka_shared.config import Settings


def _fernet(settings: Settings) -> Fernet:
    material = (settings.credential_key or settings.session_secret or "").encode("utf-8")
    digest = hashlib.sha256(b"qka-next-credential|" + material).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def protect_secret(settings: Settings, secret: str) -> bytes:
    return _fernet(settings).encrypt(secret.encode("utf-8"))


def unprotect_secret(settings: Settings, blob: bytes | None) -> str | None:
    if not blob:
        return None
    try:
        return _fernet(settings).decrypt(blob).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
