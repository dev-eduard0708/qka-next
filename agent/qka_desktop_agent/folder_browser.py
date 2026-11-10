"""Outbound folder discovery for QKA admin browse requests.

Lists directory names only — never file contents. Resolves known folders from
the assigned employee's ProfileList SID mapping. Drive roots come from live
volume discovery (not hardcoded). Does not modify NTFS ACLs. Does not recursively
enumerate whole drives unless the admin expands folders on demand.
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
    kind: str  # known_folder | directory | volume
    accessible: bool
    hasChildren: bool
    detail: str = ""


def _norm(path: Path) -> str:
    text = str(path).replace("/", "\\")
    # Keep drive-root trailing slash so "C:" and "C:\" stay distinct for clients.
    if len(text) == 2 and text[1] == ":":
        return text + "\\"
    if len(text) >= 3 and text[1] == ":" and text[2] == "\\" and text.rstrip("\\") == text[:2]:
        return text[:3]
    return text.rstrip("\\")


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
        iterator = folder.iterdir()
    except PermissionError:
        return [], "Access denied (NTFS). Scanner identity cannot list this folder."
    except OSError as exc:
        return [], f"Cannot list folder: {exc}"

    entries: list[Path] = []
    for child in iterator:
        if time.monotonic() > deadline:
            return entries, "Browse timeout."
        # Per-entry stat failures must not fail the whole listing (mixed ACLs / locked files).
        # Check reparse/junction before is_dir — profile "My Music" style junctions deny listing.
        try:
            if child.is_symlink() or _is_reparse_point(child):
                continue
            if not child.is_dir():
                continue
        except (PermissionError, OSError):
            continue
        if is_excluded_path(child):
            continue
        entries.append(child)
        if len(entries) >= remaining:
            break
    return entries, ""


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
        access_denied = False
        access_detail = ""
        for rel in candidates:
            candidate = profile.profile_path / rel
            safe = resolve_safe(candidate)
            if safe is None or not _is_within(safe, profile.profile_path):
                continue
            if is_excluded_path(safe):
                continue
            try:
                if safe.exists() and safe.is_dir():
                    found = safe
                    break
            except PermissionError:
                # Path is known but this identity cannot stat/list it — keep browsing other roots.
                found = safe
                access_denied = True
                access_detail = "Access denied."
                break
            except OSError as exc:
                found = safe
                access_denied = True
                access_detail = str(exc)[:200]
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
        accessible = not access_denied
        detail = access_detail
        if accessible:
            try:
                next(found.iterdir(), None)
            except PermissionError:
                accessible = False
                detail = "Access denied."
            except OSError as exc:
                accessible = False
                detail = str(exc)[:200]
        nodes.append(
            BrowseNode(
                name=label,
                path=_norm(found),
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
        if root.exists() and root.is_dir():
            next(root.iterdir(), None)
            root_ok = True
        else:
            root_detail = "Profile path does not exist."
    except PermissionError:
        root_detail = "Access denied."
    except OSError as exc:
        root_detail = str(exc)[:200]
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


def resolve_navigation_roots(employee_sid: str = "") -> tuple[list[BrowseNode], str]:
    """Drive roots + optional employee known folders. No recursive drive walk."""
    nodes: list[BrowseNode] = []
    try:
        from qka_desktop_agent.inventory import _collect_volumes

        for vol in _collect_volumes():
            root = str(vol.get("rootPath") or "")
            if not root:
                continue
            letter = str(vol.get("letter") or root[:2])
            label = str(vol.get("label") or "").strip()
            name = letter
            if label:
                name = f"{letter} ({label})"
            detail = ""
            cap = int(vol.get("capacityBytes") or 0)
            free = int(vol.get("freeBytes") or 0)
            if cap > 0:
                detail = f"{max(0, cap - free)}/{cap} bytes · {vol.get('fileSystem') or ''}".strip(" ·")
            nodes.append(
                BrowseNode(
                    name=name,
                    path=root,
                    kind="volume",
                    accessible=bool(vol.get("accessible", False)),
                    hasChildren=bool(vol.get("accessible", False)),
                    detail=detail,
                )
            )
    except Exception as exc:  # noqa: BLE001
        return nodes, f"Volume discovery failed: {exc}"

    if (employee_sid or "").strip():
        try:
            known, err = resolve_known_folders(employee_sid.strip())
            nodes.extend(known)
            # Known-folder errors must not hide drive roots already discovered.
            if err and not nodes:
                return nodes, err
        except Exception as exc:  # noqa: BLE001
            # Keep volumes browsable even if profile mapping throws unexpectedly.
            if not nodes:
                return nodes, f"Known folder discovery failed: {exc}"
    return nodes, ""


def browse_children(
    *,
    employee_sid: str,
    parent_path: str,
    max_entries: int = MAX_ENTRIES_DEFAULT,
    timeout_seconds: float = TIMEOUT_SECONDS_DEFAULT,
) -> tuple[list[BrowseNode], str]:
    parent_raw = (parent_path or "").strip()
    if not parent_raw:
        return resolve_navigation_roots(employee_sid)

    parent = resolve_safe(Path(parent_raw))
    if parent is None or not parent.exists() or not parent.is_dir():
        return [], "Parent folder does not exist."
    if is_excluded_path(parent):
        return [], "Parent folder is excluded (system/credential path)."

    profile = profile_for_user_sid(employee_sid) if (employee_sid or "").strip() else None
    # Allow on-demand expansion under fixed drive roots or the mapped employee profile.
    # Inaccessible children are returned with accessible=false — never fabricated.
    if not _is_under_fixed_volume(parent) and not (
        profile is not None and _is_within(parent, profile.profile_path)
    ):
        return [], "Parent folder is outside allowed browse roots."

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    children, err = _list_dir_names(parent, deadline=deadline, remaining=max(1, int(max_entries)))
    nodes: list[BrowseNode] = []
    for child in children:
        safe = resolve_safe(child)
        if safe is None:
            continue
        if profile is not None and _is_within(parent, profile.profile_path):
            if not _is_within(safe, profile.profile_path):
                continue
        elif not _is_under_fixed_volume(safe):
            continue
        if is_excluded_path(safe):
            continue
        has_kids = False
        accessible = True
        detail = ""
        try:
            for sub in safe.iterdir():
                try:
                    if sub.is_dir() and not sub.is_symlink() and not _is_reparse_point(sub):
                        has_kids = True
                        break
                except (PermissionError, OSError):
                    continue
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


def _is_under_fixed_volume(path: Path) -> bool:
    try:
        text = str(path.resolve(strict=False)).replace("/", "\\")
    except OSError:
        text = str(path).replace("/", "\\")
    if len(text) < 3 or text[1] != ":" or text[2] != "\\":
        return False
    # Reject UNC and relative escapes; fixed local drive only.
    return text[0].isalpha()


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
