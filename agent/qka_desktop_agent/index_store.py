"""Local-only SQLite index outside source folders."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileRecord:
    path: str
    size: int
    mtime: float
    sha256: str
    extension: str
    status: str  # indexed | failed | pending | removed
    error: str = ""
    extracted_chars: int = 0


class LocalIndex:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime REAL NOT NULL,
                sha256 TEXT NOT NULL,
                extension TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                extracted_chars INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get(self, path: str) -> FileRecord | None:
        row = self._conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
        if row is None:
            return None
        return FileRecord(
            path=row["path"],
            size=row["size"],
            mtime=row["mtime"],
            sha256=row["sha256"],
            extension=row["extension"],
            status=row["status"],
            error=row["error"],
            extracted_chars=row["extracted_chars"],
        )

    def upsert(self, record: FileRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO files(path, size, mtime, sha256, extension, status, error, extracted_chars, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
              size=excluded.size,
              mtime=excluded.mtime,
              sha256=excluded.sha256,
              extension=excluded.extension,
              status=excluded.status,
              error=excluded.error,
              extracted_chars=excluded.extracted_chars,
              updated_at=excluded.updated_at
            """,
            (
                record.path,
                record.size,
                record.mtime,
                record.sha256,
                record.extension,
                record.status,
                record.error,
                record.extracted_chars,
                time.time(),
            ),
        )
        self._conn.commit()

    def mark_removed_missing(self, present_paths: set[str]) -> int:
        rows = self._conn.execute("SELECT path FROM files WHERE status != 'removed'").fetchall()
        removed = 0
        for row in rows:
            if row["path"] not in present_paths:
                self._conn.execute(
                    "UPDATE files SET status='removed', updated_at=? WHERE path=?",
                    (time.time(), row["path"]),
                )
                removed += 1
        if removed:
            self._conn.commit()
        return removed

    def counts(self) -> dict:
        rows = self._conn.execute(
            "SELECT status, extension, COUNT(*) AS n FROM files WHERE status != 'removed' GROUP BY status, extension"
        ).fetchall()
        by_status = {"indexed": 0, "failed": 0, "pending": 0}
        by_type: dict[str, int] = {}
        total = 0
        for row in rows:
            status = row["status"]
            by_status[status] = by_status.get(status, 0) + int(row["n"])
            if status == "indexed":
                ext = (row["extension"] or "").lower()
                by_type[ext] = by_type.get(ext, 0) + int(row["n"])
            total += int(row["n"])
        return {
            "filesTotal": total,
            "filesIndexed": by_status.get("indexed", 0),
            "filesPending": by_status.get("pending", 0),
            "filesFailed": by_status.get("failed", 0),
            "filesByType": by_type,
        }


def file_sha256(path: Path, *, max_bytes: int = 32_000_000) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        remaining = max_bytes
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()
