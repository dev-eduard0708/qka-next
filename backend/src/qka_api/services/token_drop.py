"""Per-computer UNC token drop (ACL-bound). Never put tokens in SYSVOL/MSI/GPO."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


def sid_to_filename(computer_sid: str, *, suffix: str = ".tok") -> str:
    sid = (computer_sid or "").strip()
    safe = re.sub(r"[^0-9A-Za-z\-]", "_", sid)
    if not safe:
        raise ValueError("computer SID is required for token drop filename.")
    ext = suffix if suffix.startswith(".") else f".{suffix}"
    return f"{safe}{ext}"


def sid_to_upgrade_filename(computer_sid: str) -> str:
    """Per-computer upgrade authorization drop (distinct from first-install *.tok)."""
    return sid_to_filename(computer_sid, suffix=".upg")


def drop_dir(settings_unc: str) -> Path:
    path = (settings_unc or "").strip().rstrip("\\/")
    if not path:
        raise ValueError("QKA_AGENT_TOKEN_DROP_UNC is not configured.")
    return Path(path)


def _prefer_local_path(root: Path) -> Path:
    """Prefer a local filesystem path only when the UNC host is this computer."""
    text = str(root).replace("/", "\\")
    if not text.startswith("\\\\"):
        return root
    host = text[2:].split("\\", 1)[0].split(".", 1)[0].lower()
    me = (os.environ.get("COMPUTERNAME") or "").lower()
    if not me or host != me:
        return root
    local_token = Path(r"C:\QehcAI\QkaAgentTokens")
    if local_token.is_dir() and text.lower().rstrip("\\").endswith("qkaagenttokens"):
        return local_token
    return root


def _acl_token_file(dest: Path, computer_sid: str) -> None:
    """Replace DACL: computer SID (R,D) + Admins/SYSTEM/current-user Full.

    Uses PowerShell Set-Acl as the file owner. Unelevated API accounts often own
    the file they create but cannot run ``icacls /inheritance:r`` (needs explicit
    ChangePermissions on the parent). Owner Set-Acl works without elevation.

    Current process SID keeps FullControl so cancel/retry can delete without elevation.
    All ACEs use SIDs (not NTAccount names) to avoid IdentityNotMappedException.
    """
    sid = (computer_sid or "").strip()
    if not sid:
        raise ValueError("computer SID is required for token ACL.")
    # JSON-encode paths/SIDs for safe PowerShell literals (no secret token content here).
    path_lit = json.dumps(str(dest))
    sid_lit = json.dumps(sid)
    script = f"""
$ErrorActionPreference = 'Stop'
$path = {path_lit}
$computerSid = New-Object System.Security.Principal.SecurityIdentifier({sid_lit})
$apiSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$admins = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')
$system = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
$acl = Get-Acl -LiteralPath $path
$acl.SetAccessRuleProtection($true, $false)
foreach ($a in @($acl.Access)) {{ [void]$acl.RemoveAccessRule($a) }}
$inherit = [System.Security.AccessControl.InheritanceFlags]::None
$prop = [System.Security.AccessControl.PropagationFlags]::None
$allow = [System.Security.AccessControl.AccessControlType]::Allow
$full = [System.Security.AccessControl.FileSystemRights]::FullControl
$rd = [System.Security.AccessControl.FileSystemRights]::Read -bor [System.Security.AccessControl.FileSystemRights]::Delete
foreach ($pair in @(
  @($admins, $full),
  @($system, $full),
  @($apiSid, $full),
  @($computerSid, $rd)
)) {{
  $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($pair[0], $pair[1], $inherit, $prop, $allow)
  [void]$acl.AddAccessRule($rule)
}}
Set-Acl -LiteralPath $path -AclObject $acl
"""
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(err or "Set-Acl failed")


def stage_token_for_computer(
    *,
    drop_unc: str,
    computer_sid: str,
    computer_name: str,
    token: str,
    suffix: str = ".tok",
) -> str:
    """Write token file and grant only that computer SID Read+Delete (plus Admins/SYSTEM).

    Returns the UNC/path of the staged file. Does not log the token.
    """
    root = _prefer_local_path(drop_dir(drop_unc))
    root.mkdir(parents=True, exist_ok=True)
    name = sid_to_filename(computer_sid, suffix=suffix)
    dest = root / name
    # Replace any prior drop first — re-ACLing an existing SID-locked file can require
    # SeSecurityPrivilege that the API process may not hold under UAC filtering.
    try:
        dest.unlink(missing_ok=True)
    except OSError:
        pass
    dest.write_text(token, encoding="utf-8")
    try:
        _acl_token_file(dest, computer_sid)
    except Exception as exc:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"Failed to ACL token drop for {computer_name}: {exc}") from exc
    unc_root = (drop_unc or "").strip().rstrip("\\/")
    return f"{unc_root}\\{name}"


def remove_token_for_computer(*, drop_unc: str, computer_sid: str, suffix: str = ".tok") -> bool:
    """Delete a staged token/upgrade drop so cancelled jobs never trigger GPO action.

    Returns True when a file was removed. Missing file is success (already gone).
    Never logs token contents.
    """
    root = _prefer_local_path(drop_dir(drop_unc))
    dest = root / sid_to_filename(computer_sid, suffix=suffix)
    if not dest.is_file():
        return False
    dest.unlink(missing_ok=True)
    return True
