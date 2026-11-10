"""Agent configuration (registry / JSON). Never stores employee passwords."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("qka.agent.config")


def default_data_dir() -> Path:
    program_data = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(program_data) / "QKA" / "DesktopAgent"


def _registry_value(name: str) -> str:
    """Read HKLM\\Software\\QKA\\DesktopAgent\\<name> (MSI / policy).

    32-bit MSI writes under WOW6432Node; 64-bit agent must open that view explicitly
    or ServerBaseUrl appears missing and the service loop exits immediately.
    """
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    path = r"Software\QKA\DesktopAgent"
    access_views = (
        winreg.KEY_READ | getattr(winreg, "KEY_WOW64_32KEY", 0),
        winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        winreg.KEY_READ,
    )
    for access in access_views:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, access) as key:
                raw, _ = winreg.QueryValueEx(key, name)
            value = str(raw or "").strip()
            if value:
                return value
        except OSError:
            continue
    return ""


def _registry_server_base_url() -> str:
    return _registry_value("ServerBaseUrl")


def _registry_token_drop_unc() -> str:
    """Non-secret path only — may be set by GPO. Never a token value."""
    return _registry_value("TokenDropUnc")


def _registry_machine_enrollment_token() -> str:
    return _registry_value("MachineEnrollmentToken")


def _pending_token_path(data_dir: Path) -> Path:
    return data_dir / "pending.enrollment.token"


def _sid_token_filename(computer_sid: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z\-]", "_", (computer_sid or "").strip())
    return f"{safe}.tok"


def claim_machine_token_from_unc_drop(*, computer_sid: str, drop_unc: str) -> str:
    """Read+delete this computer's ACL-bound *.tok from the drop share.

    Requires the agent process to authenticate to SMB as the machine account
    (Network Service or SYSTEM). Local Service uses anonymous SMB and cannot claim.
    """
    root = (drop_unc or "").strip().rstrip("\\/")
    sid = (computer_sid or "").strip()
    if not root or not sid:
        return ""
    path = Path(root) / _sid_token_filename(sid)
    try:
        if not path.is_file():
            return ""
        token = path.read_text(encoding="utf-8").strip()
        try:
            path.unlink()
        except OSError:
            # Still return token; cleanup best-effort (ACL should allow Delete for this computer).
            log.warning("token_drop_delete_failed path_present=1")
        return token
    except OSError:
        return ""


def _clear_registry_machine_enrollment_token() -> None:
    """Clear HKLM token using rights Network Service should have if provisioned correctly."""
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        return
    for access in (winreg.KEY_SET_VALUE, winreg.KEY_ALL_ACCESS):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\QKA\DesktopAgent",
                0,
                access,
            ) as key:
                try:
                    winreg.DeleteValue(key, "MachineEnrollmentToken")
                except FileNotFoundError:
                    pass
                except OSError:
                    winreg.SetValueEx(key, "MachineEnrollmentToken", 0, winreg.REG_SZ, "")
            return
        except OSError:
            continue


def clear_pending_token_file(data_dir: Path) -> None:
    path = _pending_token_path(data_dir)
    try:
        if path.is_file():
            path.write_text("", encoding="utf-8")
            path.unlink(missing_ok=True)
    except OSError:
        try:
            path.write_text("", encoding="utf-8")
        except OSError:
            pass


@dataclass
class AgentConfig:
    server_base_url: str
    enrollment_code: str = ""
    machine_enrollment_token: str = ""
    token_drop_unc: str = ""
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
        # PowerShell Set-Content -Encoding UTF8 may write a BOM; strip it.
        raw = cfg_path.read_text(encoding="utf-8-sig")
        values = json.loads(raw) if raw.strip() else {}
    # Precedence: env → JSON → MSI/policy registry. Never log secrets.
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
    drop_unc = (
        os.environ.get("QKA_AGENT_TOKEN_DROP_UNC")
        or values.get("tokenDropUnc")
        or _registry_token_drop_unc()
        or ""
    ).strip()
    pending_file = _pending_token_path(data_dir)
    pending_token = ""
    if pending_file.is_file():
        try:
            pending_token = pending_file.read_text(encoding="utf-8").strip()
        except OSError:
            pending_token = ""
    machine_token = (
        os.environ.get("QKA_AGENT_MACHINE_ENROLLMENT_TOKEN")
        or values.get("machineEnrollmentToken")
        or pending_token
        or _registry_machine_enrollment_token()
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
        token_drop_unc=drop_unc.rstrip("/\\"),
        verify_tls=verify_tls,
        poll_seconds=int(values.get("pollSeconds") or 30),
        data_dir=data_dir,
    )
    if cfg.credentials_path.exists():
        creds = json.loads(cfg.credentials_path.read_text(encoding="utf-8"))
        cfg.device_id = str(creds.get("deviceId") or "")
        cfg.device_key = str(creds.get("deviceKey") or "")
    return cfg


def resolve_machine_enrollment_token(cfg: AgentConfig, computer_sid: str) -> str:
    """Prefer in-memory/config token; else claim from per-computer UNC drop (automatic)."""
    if (cfg.machine_enrollment_token or "").strip():
        return cfg.machine_enrollment_token.strip()
    if cfg.token_drop_unc and computer_sid:
        claimed = claim_machine_token_from_unc_drop(
            computer_sid=computer_sid,
            drop_unc=cfg.token_drop_unc,
        )
        if claimed:
            cfg.machine_enrollment_token = claimed
            # Stage into ProgramData so Local Service can clear its own file after enroll.
            try:
                pending = _pending_token_path(cfg.data_dir)
                pending.write_text(claimed, encoding="utf-8")
            except OSError:
                pass
            return claimed
    return ""


def save_credentials(cfg: AgentConfig, device_id: str, device_key: str) -> None:
    cfg.device_id = device_id
    cfg.device_key = device_key
    payload = {"deviceId": device_id, "deviceKey": device_key}
    cfg.credentials_path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(cfg.credentials_path, 0o600)
    except OSError:
        pass
    clear_enrollment_secrets(cfg)


def clear_enrollment_secrets(cfg: AgentConfig) -> None:
    """Remove one-time secrets from JSON, ProgramData pending file, and HKLM (best-effort)."""
    path = cfg.config_path
    if path.exists():
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            values = None
        if isinstance(values, dict):
            changed = False
            for key in ("enrollmentCode", "machineEnrollmentToken"):
                if key in values and values.get(key):
                    values[key] = ""
                    changed = True
            if changed:
                path.write_text(json.dumps(values, indent=2), encoding="utf-8")
    clear_pending_token_file(cfg.data_dir)
    _clear_registry_machine_enrollment_token()
    cfg.enrollment_code = ""
    cfg.machine_enrollment_token = ""


def device_fingerprint() -> str:
    node = uuid.getnode()
    name = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "unknown"
    return f"{name}-{node:x}"
