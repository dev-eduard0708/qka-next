"""Folder discovery: known folders, profile SID mapping, escape rejection."""

from __future__ import annotations

import os
from pathlib import Path

from qka_desktop_agent.folder_browser import browse_children, resolve_known_folders
from qka_desktop_agent.win_identity import machine_enrollment_proof


def test_machine_proof_stable():
    a = machine_enrollment_proof(token="tok", computer_sid="S-1-5-21-1-2-3-4", computer_guid="g")
    b = machine_enrollment_proof(token="tok", computer_sid="S-1-5-21-1-2-3-4", computer_guid="g")
    c = machine_enrollment_proof(token="tok", computer_sid="S-1-5-21-1-2-3-999", computer_guid="g")
    assert a == b
    assert a != c


def _profile_root(monkeypatch) -> Path:
    # Avoid pytest's default AppData\Local\Temp path — AppData is excluded by design.
    root = Path(r"C:\QehcAI\Temp\qka_browse_fixture")
    if root.exists():
        import shutil

        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QKA_TEST_PROFILE_ROOT", str(root))
    return root


def test_known_folders_from_test_profile(monkeypatch):
    tmp_path = _profile_root(monkeypatch)
    desktop = tmp_path / "Desktop"
    docs = tmp_path / "Documents"
    downs = tmp_path / "Downloads"
    desktop.mkdir()
    docs.mkdir()
    downs.mkdir()
    sid = "S-1-5-21-1-2-3-3104"
    nodes, err = resolve_known_folders(sid)
    assert err == ""
    by_name = {n.name: n for n in nodes}
    assert by_name["Desktop"].accessible is True
    assert by_name["Documents"].path.endswith("Documents")
    assert by_name["Downloads"].accessible is True


def test_browse_rejects_outside_profile(monkeypatch):
    tmp_path = _profile_root(monkeypatch)
    (tmp_path / "Documents").mkdir()
    outside = Path(r"C:\QehcAI\Temp\qka_browse_other_user")
    outside.mkdir(parents=True, exist_ok=True)
    sid = "S-1-5-21-1-2-3-3104"
    nodes, err = browse_children(employee_sid=sid, parent_path=str(outside))
    assert nodes == []
    assert "outside" in err.lower() or "profile" in err.lower()


def test_browse_children_lists_dirs_only(monkeypatch):
    tmp_path = _profile_root(monkeypatch)
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "ProjectA").mkdir()
    (docs / "secret.txt").write_text("nope", encoding="utf-8")
    sid = "S-1-5-21-1-2-3-3104"
    nodes, err = browse_children(employee_sid=sid, parent_path=str(docs))
    assert err == ""
    names = {n.name for n in nodes}
    assert names == {"ProjectA"}
    assert "secret.txt" not in names
