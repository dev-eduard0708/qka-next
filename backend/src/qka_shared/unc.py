from __future__ import annotations

import ipaddress
import re

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
SHARE_RE = re.compile(r"^[A-Za-z0-9$][A-Za-z0-9._$-]{0,79}$")


class UncError(ValueError):
    pass


def require_host(host: str | None) -> str:
    value = (host or "").strip().rstrip(".")
    if not value:
        raise UncError("NAS hostname or IP is required.")
    if "\\" in value or "/" in value:
        raise UncError("NAS host must be a hostname or IP, not a UNC path.")
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        pass
    if not HOST_RE.fullmatch(value):
        raise UncError("NAS hostname is invalid.")
    return value


def require_share(share_name: str | None) -> str:
    value = (share_name or "").strip().strip("\\/")
    if not value:
        raise UncError("Share name is required.")
    if "\\" in value or "/" in value or ":" in value:
        raise UncError("Enter the share name only (not a UNC path).")
    if not SHARE_RE.fullmatch(value):
        raise UncError("Share name is invalid.")
    return value


def normalize_folder(relative_path: str | None) -> str:
    value = (relative_path or "").replace("/", "\\").strip().strip("\\")
    if not value:
        return ""
    segments = [part for part in value.split("\\") if part]
    if any(part in {".", ".."} or ":" in part for part in segments):
        raise UncError("Shared folder path contains invalid segments.")
    return "\\".join(segments)


def build_unc(host: str, share_name: str, folder: str | None = None) -> str:
    root = f"\\\\{require_host(host)}\\{require_share(share_name)}"
    folder_path = normalize_folder(folder)
    return root if not folder_path else f"{root}\\{folder_path}"


def parse_unc(raw: str | None) -> tuple[str, str, str, str]:
    value = (raw or "").strip()
    if not value:
        raise UncError("Shared folder path is required.")
    if not value.startswith("\\\\"):
        host = require_host(value)
        return host, "", "", host
    body = value.strip("\\")
    parts = [part for part in body.split("\\") if part]
    if len(parts) < 2:
        raise UncError("Shared folder path must be \\\\SERVER\\SHARE or \\\\SERVER\\SHARE\\folder.")
    host = require_host(parts[0])
    share = require_share(parts[1])
    folder = normalize_folder("\\".join(parts[2:]))
    display = build_unc(host, share, folder)
    return host, share, folder, display


def path_inside_root(root: str, path: str) -> bool:
    root_full = root.rstrip("\\").upper() + "\\"
    path_full = path.replace("/", "\\").rstrip("\\").upper()
    if not path_full.endswith("\\"):
        path_full += "\\"
    return path_full.startswith(root_full) or path.replace("/", "\\").rstrip("\\").upper() == root.rstrip("\\").upper()
