from __future__ import annotations

import re

ALLOWED_DOMAIN = "qehc.edu.sa"
_LOCAL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+$")


def try_normalize(raw: str | None) -> tuple[str | None, str]:
    if raw is None or not raw.strip():
        return None, "Email is required."
    trimmed = raw.strip()
    if len(trimmed) > 256:
        return None, "Email is too long."
    if any(ch in trimmed for ch in (" ", "\t", "\r", "\n")):
        return None, "Email must not contain whitespace."
    if trimmed.count("@") != 1:
        return None, "Email must contain a single @."
    local, host = trimmed.split("@", 1)
    if not host.isascii() or "xn--" in host.lower():
        return None, f"Only @{ALLOWED_DOMAIN} addresses are allowed."
    if host.lower() != ALLOWED_DOMAIN:
        return None, f"Only @{ALLOWED_DOMAIN} addresses are allowed."
    if not local or local.startswith(".") or local.endswith(".") or ".." in local:
        return None, "Email local part is invalid."
    if not _LOCAL_RE.fullmatch(local):
        return None, "Email format is invalid."
    return f"{local.lower()}@{ALLOWED_DOMAIN}", ""
