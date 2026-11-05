"""Enrollment waits/retries when token is staged after first start."""

from __future__ import annotations

from qka_desktop_agent.config import AgentConfig, resolve_machine_enrollment_token


def test_resolve_claims_drop_when_config_empty(tmp_path):
    from qka_desktop_agent.config import _sid_token_filename

    sid = "S-1-5-21-3770028088-2973123715-664028206-1527"  # WSIT02 shape
    tok = tmp_path / _sid_token_filename(sid)
    tok.write_text("staged-after-msi", encoding="utf-8")
    cfg = AgentConfig(
        server_base_url="https://qka-agent.qehc.edu.sa",
        data_dir=tmp_path,
        token_drop_unc=str(tmp_path),
    )
    got = resolve_machine_enrollment_token(cfg, sid)
    assert got == "staged-after-msi"
    assert not tok.exists()


def test_resolve_empty_while_waiting(tmp_path):
    cfg = AgentConfig(
        server_base_url="https://qka-agent.qehc.edu.sa",
        data_dir=tmp_path,
        token_drop_unc=str(tmp_path),
    )
    assert resolve_machine_enrollment_token(cfg, "S-1-5-21-1-2-3-4") == ""
