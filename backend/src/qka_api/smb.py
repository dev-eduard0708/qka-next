from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from qka_shared.unc import path_inside_root

ACL_MISSING = 0
ACL_CAPTURED = 1
ACL_DENIED = 2


@dataclass(frozen=True)
class SmbCredentials:
    username: str | None
    password: str | None


@dataclass(frozen=True)
class AclSnapshot:
    captured: bool
    status: int
    entries: list[dict]
    safe_error: str | None


@dataclass(frozen=True)
class EnumeratedFile:
    relative_path: str
    file_name: str
    extension: str
    size_bytes: int
    last_write_utc: datetime | None
    acl: AclSnapshot
    is_directory: bool = False


class SmbError(RuntimeError):
    def __init__(self, message: str, diagnostics: list[str] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


def missing_acl(message: str, status: int = ACL_MISSING) -> AclSnapshot:
    return AclSnapshot(False, status, [], message)


class SmbGateway:
    def test_port(self, host: str, timeout: float = 5) -> bool:
        try:
            with socket.create_connection((host, 445), timeout=timeout):
                return True
        except OSError:
            return False

    def test_share(self, unc_root: str, credentials: SmbCredentials | None) -> tuple[bool, AclSnapshot, list[str]]:
        raise NotImplementedError

    def enumerate_files(self, unc_root: str, credentials: SmbCredentials | None) -> Iterator[EnumeratedFile]:
        raise NotImplementedError

    def open_read(self, unc_root: str, relative_path: str, credentials: SmbCredentials | None) -> bytes:
        raise NotImplementedError


class WindowsUncGateway(SmbGateway):
    def test_share(self, unc_root: str, credentials: SmbCredentials | None) -> tuple[bool, AclSnapshot, list[str]]:
        diagnostics = [f"UNC root: {_mask_unc(unc_root)}"]
        try:
            with _impersonate(credentials):
                if not os.path.isdir(unc_root):
                    diagnostics.append("Share root is not reachable with the current identity.")
                    return False, missing_acl("Share root unavailable."), diagnostics
                diagnostics.append("Share root is reachable.")
                next(os.scandir(unc_root), None)
                diagnostics.append("Share listing succeeded (metadata only).")
                return True, capture_acl(unc_root), diagnostics
        except PermissionError:
            diagnostics.append("Access denied by SMB/NTFS permissions.")
            return False, missing_acl("Access denied while probing share.", ACL_DENIED), diagnostics
        except OSError:
            diagnostics.append("I/O error while probing share.")
            return False, missing_acl("I/O error while probing share."), diagnostics

    def enumerate_files(self, unc_root: str, credentials: SmbCredentials | None) -> Iterator[EnumeratedFile]:
        with _impersonate(credentials):
            yield from _walk(unc_root, unc_root)

    def open_read(self, unc_root: str, relative_path: str, credentials: SmbCredentials | None) -> bytes:
        with _impersonate(credentials):
            full = _resolve_under_root(unc_root, relative_path)
            with open(full, "rb") as handle:
                return handle.read()


class FakeSmbGateway(SmbGateway):
    def __init__(self) -> None:
        self.port_ok = True
        self.share_ok = True
        self.raise_after: int | None = None
        self.files: list[EnumeratedFile] = []
        self.contents: dict[str, bytes] = {}
        self.calls = 0

    def test_port(self, host: str, timeout: float = 5) -> bool:
        return self.port_ok

    def test_share(self, unc_root: str, credentials: SmbCredentials | None) -> tuple[bool, AclSnapshot, list[str]]:
        if not self.share_ok:
            return False, missing_acl("Authenticated share access failed."), ["Share listing failed."]
        return True, AclSnapshot(True, ACL_CAPTURED, [{"Identity": "NAS\\pilot", "Rights": "Read", "AccessControlType": "Allow"}], None), ["Share listing succeeded (metadata only)."]

    def enumerate_files(self, unc_root: str, credentials: SmbCredentials | None) -> Iterator[EnumeratedFile]:
        for index, item in enumerate(self.files):
            if self.raise_after is not None and index >= self.raise_after:
                raise SmbError("Enumeration interrupted before completion.")
            yield item

    def open_read(self, unc_root: str, relative_path: str, credentials: SmbCredentials | None) -> bytes:
        key = relative_path.replace("/", "\\").strip("\\").upper()
        if key not in self.contents:
            raise FileNotFoundError(relative_path)
        return self.contents[key]


def capture_acl(path: str) -> AclSnapshot:
    try:
        import win32security
        import win32con
        sd = win32security.GetFileSecurity(path, win32security.DACL_SECURITY_INFORMATION)
        dacl = sd.GetSecurityDescriptorDacl()
        if dacl is None or dacl.GetAceCount() == 0:
            return missing_acl("ACL contained no readable rules.")
        entries = []
        for index in range(dacl.GetAceCount()):
            ace = dacl.GetAce(index)
            ace_type, mask, sid = ace[0][0], ace[1], ace[2]
            try:
                name, domain, _ = win32security.LookupAccountSid(None, sid)
                identity = f"{domain}\\{name}" if domain else name
            except Exception:
                identity = str(sid)
            control = "Allow" if ace_type == win32con.ACCESS_ALLOWED_ACE_TYPE else "Deny"
            entries.append({"Identity": identity, "Rights": _rights(mask), "AccessControlType": control})
        return AclSnapshot(True, ACL_CAPTURED, entries, None)
    except PermissionError:
        return missing_acl("ACL capture denied; content access defaults to denied.", ACL_DENIED)
    except Exception:
        return missing_acl("ACL could not be captured; content access defaults to denied.")


def _rights(mask: int) -> str:
    names = []
    mapping = {
        0x1: "ReadData",
        0x20: "Execute",
        0x20000: "Read",
        0x40000: "Write",
        0x80000: "Delete",
        0x10000000: "GenericAll",
        0x20000000: "GenericExecute",
        0x40000000: "GenericWrite",
        0x80000000: "GenericRead",
    }
    for bit, label in mapping.items():
        if mask & bit:
            names.append(label)
    return ", ".join(names) or hex(mask)


class _impersonate:
    def __init__(self, credentials: SmbCredentials | None):
        self.credentials = credentials
        self.handle = None

    def __enter__(self):
        creds = self.credentials
        if creds is None or not (creds.username or "").strip() or not (creds.password or ""):
            return self
        import ctypes
        from ctypes import wintypes

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        logon_user = advapi.LogonUserW
        logon_user.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        logon_user.restype = wintypes.BOOL
        token = wintypes.HANDLE()
        user, domain = _split_user(creds.username or "")
        if not logon_user(user, domain, creds.password, 9, 3, ctypes.byref(token)):
            raise SmbError("Windows network logon for the Synology service account failed.")
        impersonate = advapi.ImpersonateLoggedOnUser
        impersonate.argtypes = [wintypes.HANDLE]
        impersonate.restype = wintypes.BOOL
        if not impersonate(token):
            ctypes.windll.kernel32.CloseHandle(token)
            raise SmbError("Windows impersonation for the Synology service account failed.")
        self.handle = token
        return self

    def __exit__(self, exc_type, exc, tb):
        import ctypes
        if self.handle is not None:
            ctypes.windll.advapi32.RevertToSelf()
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None
        return False


def _split_user(username: str) -> tuple[str, str | None]:
    if "\\" in username:
        domain, user = username.split("\\", 1)
        return user, domain
    return username, None


# Synology recycle / trash directories. Must never be enumerated — scandir often fails
# with WinError 59 and would abort the entire Sync/Resync run.
_SYNOLOGY_SKIP_DIR_NAMES = frozenset(
    {
        "#recycle",
        "@recycle",
        "#recycled",
        "@recycled",
        "recycle bin",
        "$recycle.bin",
    }
)


def is_synology_recycle_dir(name: str) -> bool:
    """True for Synology (and Windows) recycle/trash folder names — always skip."""
    key = (name or "").strip().lower()
    return key in _SYNOLOGY_SKIP_DIR_NAMES


def _walk(scope_root: str, current: str) -> Iterator[EnumeratedFile]:
    if not path_inside_root(scope_root, current):
        return
    current_name = os.path.basename(current.rstrip("\\/"))
    if is_synology_recycle_dir(current_name):
        return
    try:
        entries = list(os.scandir(current))
    except PermissionError:
        return
    except OSError:
        # Unreadable / flaky network dirs must not fail the whole sync job.
        return
    for entry in entries:
        path = entry.path
        if not path_inside_root(scope_root, path):
            continue
        name = entry.name
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if is_dir and is_synology_recycle_dir(name):
            continue
        relative = path[len(scope_root.rstrip("\\")) + 1 :] if len(path) > len(scope_root.rstrip("\\")) else entry.name
        relative = relative.replace("/", "\\").lstrip("\\")
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            stat = None
        write = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc) if stat is not None else None
        if is_dir:
            yield EnumeratedFile(relative, name, "", 0, write, capture_acl(path), True)
            yield from _walk(scope_root, path)
            continue
        if stat is None:
            continue
        ext = os.path.splitext(name)[1].lstrip(".").lower()
        yield EnumeratedFile(relative, name, ext, int(stat.st_size), write, capture_acl(path), False)


def _resolve_under_root(unc_root: str, relative_path: str) -> str:
    root = os.path.abspath(unc_root.rstrip("\\") + "\\")
    relative = (relative_path or "").replace("/", "\\").lstrip("\\")
    if ".." in relative.split("\\"):
        raise PermissionError("Relative path escapes the approved Synology scope.")
    full = os.path.abspath(os.path.join(root, relative))
    if not full.lower().startswith(root.lower()):
        raise PermissionError("Resolved path escapes the approved Synology scope.")
    if not os.path.isfile(full):
        raise FileNotFoundError(relative_path)
    return full


def _mask_unc(unc: str) -> str:
    parts = [part for part in unc.lstrip("\\").split("\\") if part]
    if len(parts) >= 2:
        return f"\\\\{parts[0]}\\{parts[1]}"
    return unc


def default_gateway() -> SmbGateway:
    if os.name == "nt":
        return WindowsUncGateway()
    return WindowsUncGateway()
