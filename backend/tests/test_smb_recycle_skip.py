"""Synology #recycle must be skipped during UNC enumeration."""

from __future__ import annotations

import os
from pathlib import Path

from qka_api.smb import _walk, is_synology_recycle_dir


def test_is_synology_recycle_dir_names() -> None:
    assert is_synology_recycle_dir("#recycle") is True
    assert is_synology_recycle_dir("#Recycle") is True
    assert is_synology_recycle_dir("@recycle") is True
    assert is_synology_recycle_dir("$RECYCLE.BIN") is True
    assert is_synology_recycle_dir("EDUARDTESTCLOUD") is False
    assert is_synology_recycle_dir("Downloads") is False


def test_walk_skips_recycle_and_continues(tmp_path: Path) -> None:
    root = tmp_path / "eduard"
    keep = root / "keep"
    recycle = root / "#recycle"
    keep.mkdir(parents=True)
    recycle.mkdir(parents=True)
    (keep / "notes.txt").write_text("ok", encoding="utf-8")
    (recycle / "trashed.txt").write_text("gone", encoding="utf-8")

    items = list(_walk(str(root), str(root)))
    names = {item.file_name for item in items}
    paths = {item.relative_path.replace("/", "\\").upper() for item in items}

    assert "notes.txt" in names
    assert "trashed.txt" not in names
    assert "#RECYCLE" not in paths
    assert not any(p == "#RECYCLE" or p.startswith("#RECYCLE\\") for p in paths)


def test_walk_skips_when_current_is_recycle(tmp_path: Path) -> None:
    recycle = tmp_path / "#recycle"
    recycle.mkdir()
    (recycle / "x.txt").write_text("x", encoding="utf-8")
    assert list(_walk(str(tmp_path), str(recycle))) == []
