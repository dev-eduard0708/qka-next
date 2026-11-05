"""Outbound folder discovery for QKA admin browse requests.

Lists directory names only — never file contents. Resolves known folders from
the assigned employee's ProfileList SID mapping. Rejects escapes outside the
employee profile / approved roots. Does not modify NTFS ACLs.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from qka_desktop_agent.readonly import is_excluded_path, resolve_safe
from qka_desktop_agent.win_identity import profile_for_user_sid

KNOWN_RELATIVE = {
    "Desktop": ("Desktop",),
    "Documents": ("Documents", "My Documents"),
    "Downloads": ("Downloads",),
}

MAX_ENTRIES_DEFAULT = 200
MAX_DEPTH_DEFAULT = 8
TIMEOUT_SECONDS_DEFAULT = 8.0


@dataclass
class BrowseNode:
    name: str
    path: str
    kind: str  # known_folder | directory
    accessible: bool
    hasChildren: bool
    detail: str = ""


def _norm(path: Path) -> str:
    return str(path).replace("/", "\\").rstrip("\\")


def _is_within(child: Path, root: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (ValueError, OSError):
        return False


def _list_dir_names(folder: Path, *, deadline: float, remaining: int) -> tuple[list[Path], str]:
    if remaining <= 0:
        return [], "Result size limit reached."
    if time.monotonic() > deadline:
        return [], "Browse timeout."
    try:
        entries = []
        for child in folder.iterdir():
            if time.monotonic() > deadline:
                return entries, "Browse timeout."
            if not child.is_dir():
                continue
            if child.is_symlink() or _is_reparse_point(child):
                continue
            if is_excluded_path(child):
                continue
            entries.append(child)
            if len(entries) >= remaining:
                break
        return entries, ""
    except PermissionError:
        return [], "Access denied (NTFS). Scanner identity cannot list this folder."
    except OSError as exc:
        return [], f"Cannot list folder: {exc}"


def _is_reparse_point(path: Path) -> bool:
    if os.name != "nt":
        return path.is_symlink()
    try:
        import ctypes

        FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == -1:
            return path.is_symlink()
        return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        try:
            return path.is_symlink()
        except OSError:
            return True


def resolve_known_folders(employee_sid: str) -> tuple[list[BrowseNode], str]:
    profile = profile_for_user_sid(employee_sid)
    if profile is None:
        return [], (
            "Employee profile was not found for the assigned AD SID on this PC. "
            "The user may never have logged on locally, or ProfileList is unavailable."
        )
    if not profile.profile_path.exists():
        return [], f"Profile path does not exist: {profile.profile_path}"

    nodes: list[BrowseNode] = []
    for label, candidates in KNOWN_RELATIVE.items():
        found: Path | None = None
        for rel in candidates:
            candidate = profile.profile_path / rel
            safe = resolve_safe(candidate)
            if safe is None or not _is_within(safe, profile.profile_path):
                continue
            if safe.exists() and safe.is_dir() and not is_excluded_path(safe):
                found = safe
                break
        if found is None:
            nodes.append(
                BrowseNode(
                    name=label,
                    path="",
                    kind="known_folder",
                    accessible=False,
                    hasChildren=False,
                    detail=f"{label} not present under mapped profile.",
                )
            )
            continue
        accessible = True
        detail = ""
        try:
            next(found.iterdir(), None)
        except PermissionError:
            accessible = False
            detail = (
                "Profile folder exists but the agent identity cannot list it. "
                "Use a restricted domain service account with explicit read/list NTFS "
                "(no ACL auto-grant; no LocalSystem)."
            )
        except OSError as exc:
            accessible = False
            detail = str(exc)
        nodes.append(
            BrowseNode(
                name=label,
                path=_norm(found) if accessible else _norm(found),
                kind="known_folder",
                accessible=accessible,
                hasChildren=accessible,
                detail=detail,
            )
        )
    # Always expose profile root as expandable (still SID-mapped).
    root = profile.profile_path
    root_ok = False
    root_detail = ""
    try:
        next(root.iterdir(), None)
        root_ok = True
    except PermissionError:
        root_detail = "Agent identity cannot list the employee profile root."
    except OSError as exc:
        root_detail = str(exc)
    nodes.insert(
        0,
        BrowseNode(
            name="Profile",
            path=_norm(root),
            kind="directory",
            accessible=root_ok,
            hasChildren=root_ok,
            detail=root_detail or f"Mapped via {profile.source}",
        ),
    )
    return nodes, ""


def browse_children(
    *,
    employee_sid: str,
    parent_path: str,
    max_entries: int = MAX_ENTRIES_DEFAULT,
    timeout_seconds: float = TIMEOUT_SECONDS_DEFAULT,
) -> tuple[list[BrowseNode], str]:
    profile = profile_for_user_sid(employee_sid)
    if profile is None:
        return [], "Employee profile mapping unavailable for browse."
    parent_raw = (parent_path or "").strip()
    if not parent_raw:
        return resolve_known_folders(employee_sid)

    parent = resolve_safe(Path(parent_raw))
    if parent is None or not parent.exists() or not parent.is_dir():
        return [], "Parent folder does not exist."
    if is_excluded_path(parent):
        return [], "Parent folder is excluded (system/credential path)."
    if not _is_within(parent, profile.profile_path):
        return [], "Parent folder is outside the assigned employee profile root."

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    children, err = _list_dir_names(parent, deadline=deadline, remaining=max(1, int(max_entries)))
    nodes: list[BrowseNode] = []
    for child in children:
        safe = resolve_safe(child)
        if safe is None or not _is_within(safe, profile.profile_path):
            continue
        if is_excluded_path(safe):
            continue
        has_kids = False
        accessible = True
        detail = ""
        try:
            for sub in safe.iterdir():
                if sub.is_dir() and not sub.is_symlink():
                    has_kids = True
                    break
        except PermissionError:
            accessible = False
            detail = "Access denied."
        except OSError as exc:
            accessible = False
            detail = str(exc)
        nodes.append(
            BrowseNode(
                name=safe.name,
                path=_norm(safe),
                kind="directory",
                accessible=accessible,
                hasChildren=has_kids if accessible else False,
                detail=detail,
            )
        )
    return nodes, err


def nodes_to_json(nodes: list[BrowseNode]) -> list[dict]:
    return [
        {
            "name": n.name,
            "path": n.path,
            "kind": n.kind,
            "accessible": n.accessible,
            "hasChildren": n.hasChildren,
            "detail": n.detail,
        }
        for n in nodes
    ]
