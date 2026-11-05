"""Agent config: MSI registry URL + enrollment secret scrubbing."""

from __future__ import annotations

import json
from pathlib import Path

from qka_desktop_agent.config import AgentConfig, clear_enrollment_secrets, save_credentials


def test_clear_enrollment_secrets(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "agent.config.json"
    cfg_path.write_text(
        json.dumps({"serverBaseUrl": "https://qka-agent.qehc.edu.sa", "enrollmentCode": "SECRET"}),
        encoding="utf-8",
    )
    cfg = AgentConfig(server_base_url="https://qka-agent.qehc.edu.sa", data_dir=tmp_path, enrollment_code="SECRET")
    clear_enrollment_secrets(cfg)
    assert cfg.enrollment_code == ""
    values = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert values.get("enrollmentCode") == ""
    assert values.get("serverBaseUrl") == "https://qka-agent.qehc.edu.sa"


def test_save_credentials_scrubs_enrollment(tmp_path: Path):
    cfg_path = tmp_path / "agent.config.json"
    cfg_path.write_text(json.dumps({"enrollmentCode": "ONCE"}), encoding="utf-8")
    cfg = AgentConfig(server_base_url="https://example", data_dir=tmp_path, enrollment_code="ONCE")
    save_credentials(cfg, "dev-id", "dev-key")
    assert cfg.enrollment_code == ""
    creds = json.loads((tmp_path / "device.credentials.json").read_text(encoding="utf-8"))
    assert creds["deviceId"] == "dev-id"
    assert "ONCE" not in cfg_path.read_text(encoding="utf-8")
