"""Open authorized Synology originals in the local Windows default application.

On the API host (same machine as the browser in typical QKA deployments), write a
short-lived temp copy and launch it with os.startfile / registered associations.

No COM automation. No SMB paths returned to the client. Download Originals is
rechecked before every transfer. Remote clients fall back to an inline open URL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy.orm import Session

from qka_api.services.documents import authorize_download, read_original_bytes
from qka_api.services.office_desktop import open_with_label, recommended_app
from qka_shared.config import Settings

_TICKET_TTL_SECONDS = 90
_USED_LOCK = threading.Lock()
_USED_NONCES: dict[str, int] = {}
_TEMP_ROOT = Path(os.environ.get("TEMP", os.environ.get("TMP", "."))) / "qka-desktop-open"
_SAFE_NAME = re.compile(r"[^\w.\- ()\[\]]+", re.UNICODE)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _purge_used(now: int) -> None:
    expired = [key for key, exp in _USED_NONCES.items() if exp < now]
    for key in expired:
        _USED_NONCES.pop(key, None)


def _mark_used(nonce: str, exp: int) -> bool:
    now = int(time.time())
    with _USED_LOCK:
        _purge_used(now)
        if nonce in _USED_NONCES:
            return False
        _USED_NONCES[nonce] = exp
        return True


def _safe_filename(name: str, extension: str) -> str:
    base = (name or "document").replace("\\", "_").replace("/", "_").strip() or "document"
    base = _SAFE_NAME.sub("_", base)
    if len(base) > 120:
        base = base[:120]
    ext = (extension or "").lower().lstrip(".")
    if ext and not base.lower().endswith(f".{ext}"):
        base = f"{base}.{ext}"
    return base


def _schedule_delete(path: Path, delay_seconds: int = 600) -> None:
    def _run() -> None:
        time.sleep(max(30, delay_seconds))
        try:
            path.unlink(missing_ok=True)
            parent = path.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass

    threading.Thread(target=_run, name="qka-desktop-open-cleanup", daemon=True).start()


def _launch_local_file(path: Path) -> None:
    """Open with the Windows registered default app. Not COM automation."""
    if os.name != "nt":
        raise RuntimeError("Local desktop open requires Windows.")
    os.startfile(str(path))  # noqa: S606 — intentional ShellExecute for user-owned temp copy


def _office_protocol(extension: str, absolute_url: str) -> str | None:
    """Legacy Office URI fallback for remote clients (HTTPS). Prefer ofe + file extension in URL."""
    ext = (extension or "").lower().lstrip(".")
    if ext in {"xlsx", "xls", "xlsm", "xlsb", "csv"}:
        return f"ms-excel:ofe|u|{absolute_url}"
    if ext in {"docx", "doc"}:
        return f"ms-word:ofe|u|{absolute_url}"
    if ext in {"pptx", "ppt"}:
        return f"ms-powerpoint:ofe|u|{absolute_url}"
    return None


def _mint_token(
    *,
    document_id: str,
    caller_id: uuid.UUID,
    file_name: str,
    extension: str,
    settings: Settings,
) -> tuple[str, int]:
    exp = int(time.time()) + _TICKET_TTL_SECONDS
    nonce = secrets.token_urlsafe(12)
    payload = {
        "d": document_id,
        "u": str(caller_id),
        "e": exp,
        "n": nonce,
        "f": file_name,
        "x": extension,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    body = _b64url(raw)
    sig = hmac.new(settings.session_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}", exp


def mint_desktop_open(
    session: Session,
    settings: Settings,
    caller_id: uuid.UUID,
    document_id: str,
    *,
    public_base_url: str,
    gateway,
    prefer_local_launch: bool = True,
) -> dict[str, Any]:
    meta = authorize_download(session, settings, caller_id, document_id)
    if meta.get("denied"):
        return {
            "ok": False,
            "denied": True,
            "error": meta.get("error") or "Download Originals permission is required.",
            "requiresDownloadGrant": bool(meta.get("requiresDownloadGrant")),
        }

    file_name = meta.get("fileName") or "document"
    extension = (meta.get("extension") or "").lower().lstrip(".")
    app = recommended_app(extension)

    if prefer_local_launch and os.name == "nt":
        payload, read_meta = read_original_bytes(session, settings, caller_id, document_id, gateway)
        if payload is None:
            return {
                "ok": False,
                "denied": True,
                "error": read_meta.get("error") or "Original file could not be read.",
                "requiresDownloadGrant": bool(read_meta.get("requiresDownloadGrant")),
            }
        folder = _TEMP_ROOT / secrets.token_hex(8)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / _safe_filename(file_name, extension)
        target.write_bytes(payload)
        try:
            _launch_local_file(target)
        except OSError as exc:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            return {
                "ok": False,
                "denied": False,
                "error": f"Windows could not open the file with the default application ({exc}).",
            }
        _schedule_delete(target)
        return {
            "ok": True,
            "denied": False,
            "error": None,
            "fileName": file_name,
            "extension": extension,
            "recommendedApp": app,
            "openWithLabel": open_with_label(extension),
            "openUrl": None,
            "protocolUrl": None,
            "expiresInSeconds": None,
            "mode": "local-launch",
        }

    token, _exp = _mint_token(
        document_id=meta["id"],
        caller_id=caller_id,
        file_name=file_name,
        extension=extension,
        settings=settings,
    )
    safe_name = quote(_safe_filename(file_name, extension))
    base = (public_base_url or "").rstrip("/")
    absolute_url = f"{base}/api/v1/desktop-open/{quote(token, safe='')}/{safe_name}"
    protocol_url = _office_protocol(extension, absolute_url)
    return {
        "ok": True,
        "denied": False,
        "error": None,
        "fileName": file_name,
        "extension": extension,
        "recommendedApp": app,
        "openWithLabel": open_with_label(extension),
        "openUrl": absolute_url,
        "protocolUrl": protocol_url,
        "expiresInSeconds": _TICKET_TTL_SECONDS,
        "mode": "office-protocol" if protocol_url else "browser",
    }


def redeem_desktop_open(
    session: Session,
    settings: Settings,
    token: str,
    gateway,
) -> tuple[bytes | None, dict[str, Any]]:
    if not token or "." not in token:
        return None, {"denied": True, "error": "Invalid or expired open link."}
    # Optional trailing filename segment is stripped by the route; token is body.sig only.
    body, signature = token.rsplit(".", 1)
    expected = hmac.new(
        settings.session_secret.encode("utf-8"),
        body.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None, {"denied": True, "error": "Invalid or expired open link."}
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
        document_id = str(payload["d"])
        user_id = uuid.UUID(str(payload["u"]))
        exp = int(payload["e"])
        nonce = str(payload["n"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None, {"denied": True, "error": "Invalid or expired open link."}

    if exp < int(time.time()):
        return None, {"denied": True, "error": "Open link expired. Try Open Original again."}
    if not _mark_used(nonce, exp):
        return None, {"denied": True, "error": "Open link already used. Try Open Original again."}

    payload_bytes, meta = read_original_bytes(session, settings, user_id, document_id, gateway)
    if payload_bytes is None:
        return None, meta
    return payload_bytes, meta
