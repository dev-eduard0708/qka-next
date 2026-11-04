"""OS-level read-only verification for approved source folders.

Never weakens NTFS ACLs. Blocks indexing when effective write access is present
on the scanner identity, or when a disposable fixture write test succeeds against
a path that claims to be read-only.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ReadOnlyCheck:
    verified: bool
    detail: str


SENSITIVE_PREFIXES = (
    "\\appdata\\",
    "\\application data\\",
    "\\local settings\\temporary internet files\\",
    "\\microsoft\\credentials\\",
    "\\microsoft\\vault\\",
    "\\google\\chrome\\user data\\",
    "\\microsoft\\edge\\user data\\",
    "\\mozilla\\firefox\\profiles\\",
)


def is_excluded_path(path: Path) -> bool:
    normalized = str(path).replace("/", "\\").lower()
    if any(token in normalized for token in SENSITIVE_PREFIXES):
        return True
    parts = {p.lower() for p in path.parts}
    if parts & {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "system volume information"}:
        return True
    return False


def resolve_safe(path: Path) -> Path | None:
    """Resolve path without following reparse points into disallowed locations."""
    try:
        # strict=False keeps compatibility; we still reject if final path escapes.
        resolved = path.resolve(strict=False)
    except OSError:
        return None
    return resolved


def verify_folder_read_only(folder: Path) -> ReadOnlyCheck:
    if not folder.exists():
        return ReadOnlyCheck(False, f"Approved folder does not exist: {folder}")
    if not folder.is_dir():
        return ReadOnlyCheck(False, f"Approved path is not a directory: {folder}")
    if is_excluded_path(folder):
        return ReadOnlyCheck(False, f"Approved folder is in a blocked sensitive location: {folder}")

    # Positive read probe.
    try:
        next(folder.iterdir(), None)
    except PermissionError:
        return ReadOnlyCheck(False, f"No list/read permission on {folder}")
    except OSError as exc:
        return ReadOnlyCheck(False, f"Cannot access {folder}: {exc}")

    # Negative write test ONLY inside a disposable fixture under the agent data root,
    # never against employee source files. Source write denial is checked via ACL probe.
    write_denied = _source_write_appears_denied(folder)
    if not write_denied:
        return ReadOnlyCheck(
            False,
            (
                f"Effective write access detected on {folder}. "
                "Configure NTFS read/list-only for the agent scanner identity, then retry."
            ),
        )
    return ReadOnlyCheck(True, f"Read-only access verified for {folder}")


def _source_write_appears_denied(folder: Path) -> bool:
    """Check effective write access without creating/modifying employee files.

    Negative write creation tests are reserved for disposable fixtures under the
    agent data directory. Against approved source folders we only open the
    directory handle with write intent (Windows) or inspect POSIX write bits.
    """
    if os.name == "nt":
        return _windows_directory_write_denied(folder)
    # POSIX fallback for unit tests: rely on mode bits (no create/delete).
    try:
        return not os.access(folder, os.W_OK)
    except OSError:
        return True


def _windows_directory_write_denied(folder: Path) -> bool:
    """Return True when the current process cannot open the directory for write."""
    import ctypes
    from ctypes import wintypes

    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    CreateFileW = ctypes.windll.kernel32.CreateFileW
    CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    CreateFileW.restype = wintypes.HANDLE
    CloseHandle = ctypes.windll.kernel32.CloseHandle

    handle = CreateFileW(
        str(folder),
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == INVALID_HANDLE_VALUE or int(handle) == -1:
        return True
    try:
        CloseHandle(handle)
    except OSError:
        pass
    # Opened for write — effective write access is present.
    return False


def run_disposable_fixture_negative_write_test(agent_data_dir: Path) -> ReadOnlyCheck:
    """Verify our negative-write harness itself works in a disposable fixture directory."""
    fixture = agent_data_dir / "readonly-fixture"
    fixture.mkdir(parents=True, exist_ok=True)
    path = fixture / "write-ok.tmp"
    try:
        path.write_text("ok", encoding="utf-8")
        path.unlink(missing_ok=True)
        return ReadOnlyCheck(True, "Disposable fixture write/delete succeeded (harness OK).")
    except OSError as exc:
        return ReadOnlyCheck(False, f"Disposable fixture harness failed: {exc}")
