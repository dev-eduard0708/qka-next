"""Per-computer UNC token drop (ACL-bound). Never put tokens in SYSVOL/MSI/GPO."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def sid_to_filename(computer_sid: str) -> str:
    sid = (computer_sid or "").strip()
    safe = re.sub(r"[^0-9A-Za-z\-]", "_", sid)
    if not safe:
        raise ValueError("computer SID is required for token drop filename.")
    return f"{safe}.tok"


def drop_dir(settings_unc: str) -> Path:
    path = (settings_unc or "").strip().rstrip("\\/")
    if not path:
        raise ValueError("QKA_AGENT_TOKEN_DROP_UNC is not configured.")
    return Path(path)


def stage_token_for_computer(
    *,
    drop_unc: str,
    computer_sid: str,
    computer_name: str,
    token: str,
) -> str:
    """Write token file and grant only that computer SID Read+Delete (plus Admins/SYSTEM).

    Returns the UNC/path of the staged file. Does not log the token.
    """
    root = drop_dir(drop_unc)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / sid_to_filename(computer_sid)
    # Write privately first
    dest.write_text(token, encoding="utf-8")
    # icacls: inheritance removed; computer SID (R,D); Admins/SYSTEM full
    sid = computer_sid.strip()
    cmd = [
        "icacls",
        str(dest),
        "/inheritance:r",
        "/grant:r",
        f"*{sid}:(R,D)",
        "/grant:r",
        "BUILTIN\\Administrators:(F)",
        "/grant:r",
        "NT AUTHORITY\\SYSTEM:(F)",
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    if completed.returncode != 0:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        err = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"Failed to ACL token drop for {computer_name}: {err or 'icacls error'}"
        )
    return str(dest)
