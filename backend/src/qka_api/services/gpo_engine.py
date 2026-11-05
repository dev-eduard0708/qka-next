"""Least-privilege GPO deployment capability probe (no unrestricted remoting).

QKA never runs arbitrary PowerShell against domain PCs. Managed deploy uses an
approved computer-group / dedicated GPO path when Domain Ops has installed RSAT
GPMC and delegated the QKA deployment GPO. Until those prerequisites exist,
this engine reports unavailable and Deploy stays blocked.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MSI_PATH = Path(r"C:\QehcAI\QKA-Next\agent\dist\QKA-Agent.msi")
DEFAULT_TARGET_GROUP = "QKA-Agent-Pilot-Computers"


@dataclass(frozen=True)
class GpoEngineStatus:
    available: bool
    reason: str
    target_group: str
    msi_path: str
    msi_present: bool
    gpmc_present: bool


def _gpmc_tools_present() -> bool:
    """Detect RSAT Group Policy Management tools without writing domain state."""
    if shutil.which("Get-GPO"):
        return True
    # gpmc.msc is a snap-in, not a PATH executable — check common install locations.
    for candidate in (
        Path(r"C:\Windows\System32\gpmc.msc"),
        Path(r"C:\Windows\SysWOW64\gpmc.msc"),
    ):
        if candidate.is_file():
            return True
    try:
        import subprocess

        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "if (Get-Command Get-GPO -ErrorAction SilentlyContinue) { 'yes' } else { 'no' }",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if "yes" in (completed.stdout or "").strip().lower():
            return True
    except Exception:
        pass
    return False


def probe_gpo_engine(*, msi_path: Path | None = None, target_group: str = DEFAULT_TARGET_GROUP) -> GpoEngineStatus:
    path = msi_path or DEFAULT_MSI_PATH
    msi_present = path.is_file()
    gpmc = _gpmc_tools_present()

    if not msi_present:
        return GpoEngineStatus(
            available=False,
            reason="QKA-Agent.msi is missing on the build host.",
            target_group=target_group,
            msi_path=str(path),
            msi_present=False,
            gpmc_present=gpmc,
        )
    if not gpmc:
        return GpoEngineStatus(
            available=False,
            reason=(
                "Group Policy Management tools are not available to this QKA process. "
                "Install RSAT GPMC and delegate a dedicated QKA Agent deployment GPO "
                f"targeting only '{target_group}' (never an entire OU)."
            ),
            target_group=target_group,
            msi_path=str(path),
            msi_present=True,
            gpmc_present=False,
        )
    return GpoEngineStatus(
        available=True,
        reason="GPO tools detected. Deploy still requires HTTPS + Super Admin approval + computer-group membership.",
        target_group=target_group,
        msi_path=str(path),
        msi_present=True,
        gpmc_present=True,
    )


def build_gpo_package_manifest(
    *,
    computer_names: list[str],
    server_base_url: str,
    msi_path: str,
    target_group: str,
) -> dict:
    """Describe the intended GPO Software Installation package (no domain writes)."""
    names = [n.strip() for n in computer_names if (n or "").strip()]
    if not names:
        raise ValueError("Select at least one computer for deployment.")
    if len(names) > 50:
        raise ValueError("Bulk deployment is limited to 50 computers per approval batch.")
    return {
        "packageType": "msi_software_installation",
        "msiPath": msi_path,
        "silentArgs": f'/qn QKASERVERURL={server_base_url}',
        "targetGroup": target_group,
        "computers": names,
        "scopeRule": "computer_group_only",
        "forbidden": ["entire_ou", "all_domain", "unrestricted_psexec", "unrestricted_winrm"],
        "note": (
            "QKA records the job and machine-bound enrollment token. Domain Ops applies "
            "the dedicated GPO to the approved computer group only."
        ),
    }
