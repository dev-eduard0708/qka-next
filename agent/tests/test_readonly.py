"""Read-only scanner identity checks (disposable fixtures only)."""

from __future__ import annotations

from pathlib import Path

from qka_desktop_agent.readonly import (
    is_excluded_path,
    run_disposable_fixture_negative_write_test,
    verify_folder_read_only,
)


def test_sensitive_paths_excluded():
    assert is_excluded_path(Path(r"C:\Users\alice\AppData\Roaming\x"))
    assert is_excluded_path(Path(r"C:\Windows\System32"))
    assert not is_excluded_path(Path(r"C:\QKA-Pilot-Docs\projects"))


def test_verify_missing_folder_blocks(tmp_path: Path):
    missing = tmp_path / "nope"
    check = verify_folder_read_only(missing)
    assert check.verified is False
    assert "does not exist" in check.detail


def test_disposable_fixture_harness(tmp_path: Path):
    check = run_disposable_fixture_negative_write_test(tmp_path)
    assert check.verified is True
    assert "fixture" in check.detail.lower()
