"""Per-computer UNC token-drop helpers (no live share writes in CI)."""

from __future__ import annotations

from qka_api.services.token_drop import sid_to_filename


def test_sid_to_filename_sanitizes():
    assert sid_to_filename("S-1-5-21-1-2-3-4") == "S-1-5-21-1-2-3-4.tok"
    assert ".." not in sid_to_filename("S-1-5-21-1/2\\3")
    assert sid_to_filename("S-1-5-21-1/2\\3").endswith(".tok")


def test_agent_claim_missing_file(tmp_path, monkeypatch):
    from qka_desktop_agent.config import claim_machine_token_from_unc_drop

    got = claim_machine_token_from_unc_drop(
        computer_sid="S-1-5-21-9-9-9-9",
        drop_unc=str(tmp_path),
    )
    assert got == ""


def test_agent_claim_and_delete(tmp_path):
    from qka_desktop_agent.config import claim_machine_token_from_unc_drop, _sid_token_filename

    sid = "S-1-5-21-111-222-333-444"
    path = tmp_path / _sid_token_filename(sid)
    path.write_text("one-time-secret-token-value", encoding="utf-8")
    got = claim_machine_token_from_unc_drop(computer_sid=sid, drop_unc=str(tmp_path))
    assert got == "one-time-secret-token-value"
    assert not path.exists()
