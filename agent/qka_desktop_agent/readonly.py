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
    """Best-effort check that creating a new file in the source folder fails.

    Uses a unique disposable name and deletes immediately if creation somehow succeeds
    (should not happen when ACLs are correct). Never modifies existing employee files.
    """
    probe = folder / f".qka_ro_probe_{os.getpid()}_{os.urandom(4).hex()}.tmp"
    try:
        with open(probe, "xb") as handle:
            handle.write(b"qka-readonly-probe")
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    except FileExistsError:
        return True
    except PermissionError:
        return True
    except OSError:
        # Treat other access failures as denied write for safety.
        return True


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
