"""Resolve local Windows machine and user profile identities (no fake paths)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalMachineIdentity:
    computer_sid: str
    computer_name: str
    source: str


@dataclass(frozen=True)
class UserProfile:
    sid: str
    profile_path: Path
    source: str


_SID_RE = re.compile(r"^S-1-5-(?:21|32)-\d+(?:-\d+)+$", re.IGNORECASE)


def local_computer_sid() -> LocalMachineIdentity:
    """Read this computer's SID from the OS — never from config or env."""
    name = (os.environ.get("COMPUTERNAME") or "").strip()
    if os.name != "nt":
        # Non-Windows unit harness only.
        return LocalMachineIdentity(computer_sid="", computer_name=name, source="non_windows")
    try:
        import ctypes
        from ctypes import wintypes

        # Prefer LookupAccountNameW for COMPUTERNAME$
        advapi = ctypes.windll.advapi32
        LookupAccountNameW = advapi.LookupAccountNameW
        GetComputerNameW = ctypes.windll.kernel32.GetComputerNameW

        buf = ctypes.create_unicode_buffer(256)
        size = wintypes.DWORD(256)
        if GetComputerNameW(buf, ctypes.byref(size)):
            name = buf.value

        account = f"{name}$"
        sid_size = wintypes.DWORD(0)
        domain_size = wintypes.DWORD(0)
        pe_use = wintypes.DWORD(0)
        LookupAccountNameW(None, account, None, ctypes.byref(sid_size), None, ctypes.byref(domain_size), ctypes.byref(pe_use))
        if sid_size.value <= 0:
            raise OSError("LookupAccountName size failed")
        sid_buf = ctypes.create_string_buffer(sid_size.value)
        domain_buf = ctypes.create_unicode_buffer(domain_size.value or 1)
        if not LookupAccountNameW(
            None,
            account,
            sid_buf,
            ctypes.byref(sid_size),
            domain_buf,
            ctypes.byref(domain_size),
            ctypes.byref(pe_use),
        ):
            raise OSError("LookupAccountName failed")
        ConvertSidToStringSidW = advapi.ConvertSidToStringSidW
        ConvertSidToStringSidW.restype = wintypes.BOOL
        ptr = wintypes.LPWSTR()
        if not ConvertSidToStringSidW(sid_buf, ctypes.byref(ptr)):
            raise OSError("ConvertSidToStringSid failed")
        try:
            sid = ptr.value or ""
        finally:
            ctypes.windll.kernel32.LocalFree(ptr)
        if not _SID_RE.match(sid or ""):
            raise OSError(f"Unexpected computer SID: {sid}")
        return LocalMachineIdentity(computer_sid=sid, computer_name=name, source="lookup_account_name")
    except Exception:
        # Fallback: ProfileList machine account is not listed; leave empty → enroll must fail closed for machine mode.
        return LocalMachineIdentity(computer_sid="", computer_name=name, source="unavailable")


def profile_for_user_sid(user_sid: str) -> UserProfile | None:
    """Map AD user SID → local profile path via ProfileList (not C:\\Users\\<username>)."""
    sid = (user_sid or "").strip()
    if not _SID_RE.match(sid):
        return None
    # Test harness only — never used as production path mapping.
    root = os.environ.get("QKA_TEST_PROFILE_ROOT", "").strip()
    if root:
        return UserProfile(sid=sid, profile_path=Path(root), source="test_env")
    if os.name != "nt":
        return None
    try:
        import winreg

        key_path = rf"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\{sid}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            raw, _ = winreg.QueryValueEx(key, "ProfileImagePath")
        expanded = os.path.expandvars(str(raw or "")).strip().strip('"')
        if not expanded:
            return None
        path = Path(expanded)
        # Refuse other users: SID key already scopes to one profile.
        return UserProfile(sid=sid, profile_path=path, source="profile_list")
    except OSError:
        return None


def machine_enrollment_proof(*, token: str, computer_sid: str, computer_guid: str = "") -> str:
    """HMAC proof that the caller holds the one-time token and local machine SID."""
    import hashlib
    import hmac as hm

    msg = f"{(computer_sid or '').strip()}|{(computer_guid or '').strip()}".encode("utf-8")
    digest = hm.new((token or "").encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return digest
