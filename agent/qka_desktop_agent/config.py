"""Agent configuration (registry / JSON). Never stores employee passwords."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path


def default_data_dir() -> Path:
    program_data = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(program_data) / "QKA" / "DesktopAgent"


def _registry_server_base_url() -> str:
    """Read MSI-written HKLM\\Software\\QKA\\DesktopAgent\\ServerBaseUrl (QKASERVERURL)."""
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"Software\QKA\DesktopAgent") as key:
            raw, _ = winreg.QueryValueEx(key, "ServerBaseUrl")
        return str(raw or "").strip()
    except OSError:
        return ""


@dataclass
class AgentConfig:
    server_base_url: str
    enrollment_code: str = ""
    machine_enrollment_token: str = ""
    device_id: str = ""
    device_key: str = ""
    verify_tls: bool = True
    poll_seconds: int = 30
    data_dir: Path = Path()

    @property
    def credentials_path(self) -> Path:
        return self.data_dir / "device.credentials.json"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "agent.config.json"

    @property
    def index_path(self) -> Path:
        return self.data_dir / "index" / "local.sqlite3"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "logs" / "agent.log"


def load_config() -> AgentConfig:
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "index").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    cfg_path = data_dir / "agent.config.json"
    values: dict = {}
    if cfg_path.exists():
        values = json.loads(cfg_path.read_text(encoding="utf-8"))
    # Precedence: env → JSON → MSI registry (QKASERVERURL). Never log secrets.
    server = (
        os.environ.get("QKA_AGENT_SERVER_URL")
        or values.get("serverBaseUrl")
        or _registry_server_base_url()
        or ""
    ).strip()
    enrollment = (
        os.environ.get("QKA_AGENT_ENROLLMENT_CODE")
        or values.get("enrollmentCode")
        or ""
    ).strip()
    machine_token = (
        os.environ.get("QKA_AGENT_MACHINE_ENROLLMENT_TOKEN")
        or values.get("machineEnrollmentToken")
        or ""
    ).strip()
    verify_tls = str(os.environ.get("QKA_AGENT_VERIFY_TLS", values.get("verifyTls", True))).lower() not in {
        "0",
        "false",
        "no",
    }
    cfg = AgentConfig(
        server_base_url=server.rstrip("/"),
        enrollment_code=enrollment,
        machine_enrollment_token=machine_token,
        verify_tls=verify_tls,
        poll_seconds=int(values.get("pollSeconds") or 30),
        data_dir=data_dir,
    )
    if cfg.credentials_path.exists():
        creds = json.loads(cfg.credentials_path.read_text(encoding="utf-8"))
        cfg.device_id = str(creds.get("deviceId") or "")
        cfg.device_key = str(creds.get("deviceKey") or "")
    return cfg


def save_credentials(cfg: AgentConfig, device_id: str, device_key: str) -> None:
    cfg.device_id = device_id
    cfg.device_key = device_key
    payload = {"deviceId": device_id, "deviceKey": device_key}
    cfg.credentials_path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(cfg.credentials_path, 0o600)
    except OSError:
        pass
    # Drop one-time enrollment secrets from JSON after successful enroll (never log them).
    clear_enrollment_secrets(cfg)


def clear_enrollment_secrets(cfg: AgentConfig) -> None:
    """Remove enrollmentCode / machineEnrollmentToken from agent.config.json after use."""
    path = cfg.config_path
    if not path.exists():
        cfg.enrollment_code = ""
        cfg.machine_enrollment_token = ""
        return
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    changed = False
    for key in ("enrollmentCode", "machineEnrollmentToken"):
        if key in values and values.get(key):
            values[key] = ""
            changed = True
    if changed:
        path.write_text(json.dumps(values, indent=2), encoding="utf-8")
    cfg.enrollment_code = ""
    cfg.machine_enrollment_token = ""


def device_fingerprint() -> str:
    # Stable-ish machine identity without requiring domain admin APIs.
    node = uuid.getnode()
    name = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "unknown"
    return f"{name}-{node:x}"
