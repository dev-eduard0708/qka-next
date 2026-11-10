"""Read-only incremental scanner for approved local folders."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from qka_desktop_agent.index_store import FileRecord, LocalIndex, file_sha256
from qka_desktop_agent.readonly import is_excluded_path, resolve_safe, verify_folder_read_only

log = logging.getLogger("qka.agent.scanner")

SUPPORTED = {"pdf", "docx", "xlsx", "txt", "csv"}
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_reparse_point(path: Path) -> bool:
    """Skip junctions/symlinks (e.g. Documents\\My Music) — do not follow outside approved trees."""
    if os.name != "nt":
        try:
            return path.is_symlink()
        except OSError:
            return True
    try:
        import ctypes

        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == -1:
            return path.is_symlink()
        return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        try:
            return path.is_symlink()
        except OSError:
            return True


def _iter_files(root: Path, *, include_subfolders: bool) -> Iterator[Path]:
    """Walk approved folders without following reparse points (Path.glob follows junctions)."""
    if include_subfolders:
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                entries = list(current.iterdir())
            except PermissionError:
                log.info("skip_inaccessible_dir path=%s", current)
                continue
            except OSError as exc:
                log.info("skip_dir_error path=%s err=%s", current, exc)
                continue
            for child in entries:
                try:
                    if _is_reparse_point(child):
                        continue
                    if child.is_dir():
                        stack.append(child)
                    elif child.is_file():
                        yield child
                except OSError:
                    continue
        return
    try:
        for child in root.iterdir():
            try:
                if _is_reparse_point(child):
                    continue
                if child.is_file():
                    yield child
            except OSError:
                continue
    except OSError as exc:
        log.info("skip_root_list path=%s err=%s", root, exc)


def _busy() -> bool:
    """Throttle when the machine looks busy (simple CPU load heuristic)."""
    try:
        import psutil  # optional

        return float(psutil.cpu_percent(interval=0.1)) >= 85.0
    except Exception:
        # Fallback: Windows GetSystemTimes via ctypes is heavier; skip throttle.
        return False


def extract_text(path: Path) -> tuple[str, str | None]:
    """Reuse QKA extraction when available; otherwise lightweight local readers."""
    try:
        from qka_api.extract import extract_bytes

        data = path.read_bytes()
        status, text, error, _digest = extract_bytes(path.suffix.lstrip("."), data, max_chars=200_000)
        if status == 1:
            return text or "", None
        return "", error or "extraction_failed"
    except Exception:
        pass
    ext = path.suffix.lower().lstrip(".")
    if ext in {"txt", "csv"}:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:200_000], None
        except OSError as exc:
            return "", str(exc)
    return "", "extractor_unavailable"


def scan_once(
    index: LocalIndex,
    folders: list[dict],
    *,
    should_pause: Callable[[], bool] | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    present: set[str] = set()
    processed = 0
    verified_all = True
    details: list[str] = []

    for folder in folders:
        raw = Path(folder["path"])
        check = verify_folder_read_only(raw)
        details.append(check.detail)
        if not check.verified:
            verified_all = False
            continue
        include_sub = bool(folder.get("includeSubfolders", True))
        root = resolve_safe(raw)
        if root is None:
            verified_all = False
            details.append(f"Could not resolve {raw}")
            continue
        for path in _iter_files(root, include_subfolders=include_sub):
            if should_pause and should_pause():
                counts = index.counts()
                counts.update(
                    {
                        "indexingState": "paused",
                        "readOnlyVerified": verified_all,
                        "readOnlyDetail": "; ".join(details)[:1000],
                    }
                )
                if on_progress:
                    on_progress(counts)
                return counts
            while _busy():
                time.sleep(2.0)
                if should_pause and should_pause():
                    break
            if is_excluded_path(path):
                continue
            resolved = resolve_safe(path)
            if resolved is None or not str(resolved).lower().startswith(str(root).lower()):
                continue
            ext = resolved.suffix.lower().lstrip(".")
            if ext not in SUPPORTED:
                continue
            key = str(resolved)
            present.add(key)
            try:
                stat = resolved.stat()
            except OSError as exc:
                index.upsert(
                    FileRecord(key, 0, 0, "", ext, "failed", error=str(exc))
                )
                processed += 1
                continue
            existing = index.get(key)
            if (
                existing
                and existing.status == "indexed"
                and existing.size == stat.st_size
                and abs(existing.mtime - stat.st_mtime) < 0.001
            ):
                processed += 1
                continue
            try:
                digest = file_sha256(resolved)
                text, error = extract_text(resolved)
                if error:
                    index.upsert(
                        FileRecord(
                            key,
                            stat.st_size,
                            stat.st_mtime,
                            digest,
                            ext,
                            "failed",
                            error=error,
                        )
                    )
                else:
                    index.upsert(
                        FileRecord(
                            key,
                            stat.st_size,
                            stat.st_mtime,
                            digest,
                            ext,
                            "indexed",
                            extracted_chars=len(text or ""),
                        )
                    )
            except OSError as exc:
                index.upsert(
                    FileRecord(key, stat.st_size, stat.st_mtime, "", ext, "failed", error=str(exc))
                )
            processed += 1
            if on_progress and processed % 25 == 0:
                counts = index.counts()
                counts.update(
                    {
                        "indexingState": "running",
                        "readOnlyVerified": verified_all,
                        "readOnlyDetail": "; ".join(details)[:1000],
                    }
                )
                on_progress(counts)

    removed = index.mark_removed_missing(present)
    log.info("scan_complete processed=%s removed=%s verified=%s", processed, removed, verified_all)
    counts = index.counts()
    counts.update(
        {
            "indexingState": "idle" if verified_all else "error",
            "readOnlyVerified": verified_all,
            "readOnlyDetail": "; ".join(details)[:1000],
            "lastError": "" if verified_all else "Read-only verification failed for one or more folders.",
        }
    )
    if on_progress:
        on_progress(counts)
    return counts
