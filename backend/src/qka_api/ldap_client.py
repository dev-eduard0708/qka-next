"""Active Directory LDAP client for QKA.

Modes:
  - windows_integrated (recommended on domain-joined Windows): Kerberos/SSPI
    with LDAP signing + sealing on port 389. Uses the process identity.
  - ldaps (optional): simple bind over TLS with certificate validation required.

Anonymous binds, unsecured simple binds, and silent auth downgrades are rejected.
Passwords are never logged. Gateway is injectable for tests.
"""

from __future__ import annotations

import os
import re
import socket
import ssl
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

AUTH_WINDOWS_INTEGRATED = "windows_integrated"
AUTH_LDAPS = "ldaps"

# ADSI flags — secure auth + integrity + confidentiality (no cleartext LDAP).
ADS_SECURE_AUTHENTICATION = 0x1
ADS_USE_SIGNING = 0x40
ADS_USE_SEALING = 0x80
ADS_SERVER_BIND = 0x200
_WINDOWS_ADSI_FLAGS = (
    ADS_SECURE_AUTHENTICATION | ADS_USE_SIGNING | ADS_USE_SEALING | ADS_SERVER_BIND
)

# FastAPI runs sync routes on worker threads. ADSI/COM requires per-thread CoInitialize.
_thread_com = threading.local()


class LdapError(Exception):
    def __init__(self, message: str, *, diagnostics: list[str] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


def ensure_windows_com() -> str:
    """Initialize COM on the current thread for ADSI/ADO (idempotent per thread).

    Returns the apartment mode used: 'mta', 'sta', 'already', or 'n/a'.
    """
    if os.name != "nt":
        return "n/a"
    status = getattr(_thread_com, "status", None)
    if status:
        return status
    try:
        import pythoncom
    except ImportError as exc:
        raise LdapError("pywin32/pythoncom is required for Windows Integrated LDAP.") from exc
    try:
        # Worker threads (uvicorn/FastAPI threadpool) should use MTA.
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        _thread_com.status = "mta"
    except Exception:
        try:
            pythoncom.CoInitialize()
            _thread_com.status = "sta"
        except Exception:
            # Already initialized on this thread with a compatible mode.
            _thread_com.status = "already"
    return _thread_com.status


@dataclass
class CheckResult:
    name: str
    status: str  # pass | failed | not_tested
    detail: str = ""


@dataclass
class AdUserRecord:
    object_guid: str
    object_sid: str
    sam_account_name: str
    user_principal_name: str
    display_name: str
    mail: str
    department: str
    distinguished_name: str
    account_enabled: bool


@dataclass
class AdGroupRecord:
    object_guid: str
    object_sid: str
    name: str
    distinguished_name: str
    is_security_group: bool
    member_guids: list[str] = field(default_factory=list)
    member_sids: list[str] = field(default_factory=list)


@dataclass
class AdOuNode:
    name: str
    distinguished_name: str
    children: list[AdOuNode] = field(default_factory=list)


@dataclass
class LdapConnectParams:
    domain_dns_name: str
    domain_controller: str
    port: int = 389
    bind_username: str = ""
    bind_password: str = ""
    base_dn: str = ""
    timeout_seconds: int = 15
    verify_tls: bool = True
    auth_mode: str = AUTH_WINDOWS_INTEGRATED


_LDAP_SPECIAL = re.compile(r"([\\*()+\x00])")


def escape_ldap_filter(value: str) -> str:
    """RFC 4515 filter escaping — never interpolate untrusted input raw."""
    return _LDAP_SPECIAL.sub(lambda m: "\\%02x" % ord(m.group(1)), value or "")


def escape_ldap_dn(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace(",", "\\,")


def normalize_auth_mode(value: str | None) -> str:
    raw = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"windows_integrated", "windows", "sspi", "kerberos", "integrated"}:
        return AUTH_WINDOWS_INTEGRATED
    if raw in {AUTH_LDAPS, "ldap_tls", "tls"}:
        return AUTH_LDAPS
    if raw in {"ldap", "starttls", "plain"}:
        raise LdapError("Unsecured LDAP is not allowed. Use Windows Integrated or LDAPS.")
    return AUTH_WINDOWS_INTEGRATED


def current_windows_identity() -> dict[str, str]:
    """Effective Windows identity of the QKA process (never a password)."""
    identity = {
        "samCompatible": "",
        "userPrincipalName": "",
        "dnsDomain": "",
        "username": os.environ.get("USERNAME") or "",
        "userDomain": os.environ.get("USERDOMAIN") or "",
        "computerName": os.environ.get("COMPUTERNAME") or "",
    }
    if os.name != "nt":
        return identity
    try:
        import win32api

        # NameSamCompatible=2, NameUserPrincipal=8, NameDnsDomain=12
        for key, fmt in (("samCompatible", 2), ("userPrincipalName", 8), ("dnsDomain", 12)):
            try:
                identity[key] = str(win32api.GetUserNameEx(fmt) or "")
            except Exception:
                pass
    except Exception:
        pass
    if not identity["samCompatible"] and identity["userDomain"] and identity["username"]:
        identity["samCompatible"] = f"{identity['userDomain']}\\{identity['username']}"
    return identity


def domain_dns_to_base_dn(domain_dns_name: str) -> str:
    parts = [p for p in (domain_dns_name or "").strip(".").split(".") if p]
    return ",".join(f"DC={p}" for p in parts)


def resolve_directory_host(params: LdapConnectParams) -> tuple[str, str]:
    """Return (host, how) — explicit DC, else domain DNS, else local RootDSE dnsHostName."""
    explicit = (params.domain_controller or "").strip()
    if explicit:
        return explicit, "configured"
    domain = (params.domain_dns_name or "").strip()
    if domain:
        return domain, "domain_dns"
    if os.name == "nt":
        try:
            import win32com.client

            root = win32com.client.GetObject("LDAP://RootDSE")
            host = str(root.Get("dnsHostName") or "")
            if host:
                return host, "rootdse_autodetect"
        except Exception:
            pass
    raise LdapError("Domain controller or domain DNS name is required.")


class LdapGateway(Protocol):
    def diagnose(self, params: LdapConnectParams) -> tuple[bool, str | None, list[CheckResult], str]:
        """Returns (succeeded, safe_error, checks, discovered_base_dn)."""

    def list_ous(self, params: LdapConnectParams, *, parent_dn: str = "", search: str = "") -> list[AdOuNode]:
        ...

    def search_users(
        self, params: LdapConnectParams, *, ou_dns: list[str], limit: int = 500
    ) -> list[AdUserRecord]:
        ...

    def search_groups(
        self, params: LdapConnectParams, *, ou_dns: list[str], limit: int = 500
    ) -> list[AdGroupRecord]:
        ...


def _guid_to_str(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, bytearray):
        raw = bytes(raw)
    if isinstance(raw, bytes) and len(raw) == 16:
        import uuid as _uuid

        return str(_uuid.UUID(bytes_le=raw))
    return str(raw)


def _sid_to_str(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, bytearray):
        raw = bytes(raw)
    if isinstance(raw, bytes) and len(raw) >= 8:
        revision = raw[0]
        count = raw[1]
        authority = int.from_bytes(raw[2:8], "big")
        subs = []
        offset = 8
        for _ in range(count):
            if offset + 4 > len(raw):
                break
            subs.append(str(int.from_bytes(raw[offset : offset + 4], "little")))
            offset += 4
        return f"S-{revision}-{authority}-" + "-".join(subs)
    text = str(raw)
    return text if text.startswith("S-") else text


def _uac_enabled(uac: Any) -> bool:
    try:
        return (int(uac) & 0x2) == 0
    except (TypeError, ValueError):
        return True


def _ado_scalar(value: Any) -> Any:
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return value[0]
    return value


def _com_hresult(exc: BaseException) -> str | None:
    hr = getattr(exc, "hresult", None)
    if isinstance(hr, int):
        return f"0x{hr & 0xFFFFFFFF:08X}"
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return f"0x{args[0] & 0xFFFFFFFF:08X}"
    if args and isinstance(args[0], tuple) and args[0] and isinstance(args[0][0], int):
        return f"0x{args[0][0] & 0xFFFFFFFF:08X}"
    return None


def _safe_kerberos_error(exc: BaseException) -> str:
    """Sanitized Kerberos/COM failure for UI — never includes secrets/tokens."""
    hr = _com_hresult(exc)
    text = str(exc)
    lowered = text.lower()
    # MK_E_SYNTAX — typically COM not initialized on this thread (FastAPI worker).
    if hr == "0x800401E4" or "invalid syntax" in lowered:
        suffix = f" ({hr})" if hr else ""
        return (
            "Kerberos/SSPI bind failed because the Windows COM/ADSI context was not "
            f"initialized on the API request thread{suffix}."
        )
    if "logon failure" in lowered or "8009030c" in lowered:
        suffix = f" ({hr})" if hr else ""
        return f"Kerberos/SSPI authentication failed for the Windows service identity{suffix}."
    if "access is denied" in lowered or hr == "0x80070005":
        suffix = f" ({hr})" if hr else ""
        return f"Access denied — the Windows service identity lacks directory read permission{suffix}."
    if "server is not operational" in lowered or "can't contact" in lowered:
        suffix = f" ({hr})" if hr else ""
        return f"Directory server is not reachable for Windows-integrated LDAP{suffix}."
    if "gssapi" in lowered or "kerberos" in lowered or "sspi" in lowered:
        suffix = f" ({hr})" if hr else ""
        return f"Kerberos/SSPI authentication failed{suffix}."
    suffix = f" ({hr})" if hr else ""
    return f"Windows-integrated LDAP authentication failed{suffix}."


class RealLdapGateway:
    """Production AD gateway: Windows Integrated SSPI (default) or LDAPS."""

    def diagnose(self, params: LdapConnectParams) -> tuple[bool, str | None, list[CheckResult], str]:
        mode = normalize_auth_mode(params.auth_mode)
        if mode == AUTH_WINDOWS_INTEGRATED:
            return self._diagnose_windows_integrated(params)
        return self._diagnose_ldaps(params)

    def _diagnose_windows_integrated(
        self, params: LdapConnectParams
    ) -> tuple[bool, str | None, list[CheckResult], str]:
        checks: list[CheckResult] = [
            CheckResult("dns", "not_tested"),
            CheckResult("tcp", "not_tested"),
            CheckResult("identity", "not_tested"),
            CheckResult("kerberos", "not_tested"),
            CheckResult("signing", "not_tested"),
            CheckResult("sealing", "not_tested"),
            CheckResult("rootdse", "not_tested"),
            CheckResult("search", "not_tested"),
        ]
        if os.name != "nt":
            checks[2].status = "failed"
            checks[2].detail = "Windows Integrated mode requires a domain-joined Windows host."
            return False, checks[2].detail, checks, ""

        identity = current_windows_identity()
        display = identity.get("samCompatible") or identity.get("userPrincipalName") or identity.get("username") or "(unknown)"
        checks[2].status = "pass"
        checks[2].detail = f"Service identity: {display}"

        try:
            host, how = resolve_directory_host(params)
        except LdapError as exc:
            checks[0].status = "failed"
            checks[0].detail = str(exc)
            return False, str(exc), checks, ""

        port = int(params.port or 389)
        if port == 636:
            # Windows Integrated must not depend on LDAPS/636.
            port = 389

        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            checks[0].status = "pass"
            checks[0].detail = f"Resolved {host} → {infos[0][4][0]} ({how})"
        except OSError:
            checks[0].status = "failed"
            checks[0].detail = "Domain DNS resolution failed."
            return False, "Domain DNS resolution failed.", checks, ""

        try:
            with socket.create_connection((host, port), timeout=params.timeout_seconds):
                pass
            checks[1].status = "pass"
            checks[1].detail = f"TCP {port} reachable."
        except OSError:
            checks[1].status = "failed"
            checks[1].detail = f"TCP {port} is not reachable."
            return False, checks[1].detail, checks, ""

        # Signing/sealing stay not_tested until Kerberos/SSPI bind actually succeeds.
        discovered = (params.base_dn or "").strip()
        try:
            com_mode = ensure_windows_com()
            import win32com.client

            dso = win32com.client.GetObject("LDAP:")
            root = dso.OpenDSObject(f"LDAP://{host}/RootDSE", "", "", _WINDOWS_ADSI_FLAGS)
            discovered = str(root.Get("defaultNamingContext") or discovered or "")
            dc_host = str(root.Get("dnsHostName") or host)
            checks[3].status = "pass"
            checks[3].detail = (
                f"Kerberos/SSPI bind succeeded as {display} → {dc_host} (COM {com_mode})"
            )
            # Only after a successful signed+sealed OpenDSObject bind.
            checks[4].status = "pass"
            checks[4].detail = "LDAP signing negotiated (ADS_USE_SIGNING)."
            checks[5].status = "pass"
            checks[5].detail = "LDAP sealing negotiated (ADS_USE_SEALING)."
            checks[6].status = "pass"
            checks[6].detail = f"Base DN: {discovered or '(empty)'}"
            if not discovered:
                checks[6].status = "failed"
                checks[6].detail = "RootDSE did not return a naming context."
                return False, checks[6].detail, checks, ""
        except Exception as exc:
            safe = _safe_kerberos_error(exc)
            checks[3].status = "failed"
            checks[3].detail = safe
            checks[4].status = "failed"
            checks[4].detail = "LDAP signing not negotiated — authentication did not succeed."
            checks[5].status = "failed"
            checks[5].detail = "LDAP sealing not negotiated — authentication did not succeed."
            checks[6].status = "failed"
            checks[6].detail = "RootDSE discovery failed."
            return False, safe, checks, ""

        try:
            rows = self._ado_search(
                host,
                discovered,
                "(objectClass=organizationalUnit)",
                ["ou", "distinguishedName"],
                scope="onelevel",
                limit=5,
                timeout=params.timeout_seconds,
            )
            checks[7].status = "pass"
            checks[7].detail = f"Directory search succeeded ({len(rows)} OU sample(s))."
        except Exception as exc:
            checks[7].status = "failed"
            checks[7].detail = "Directory base search failed."
            return False, _safe_kerberos_error(exc), checks, discovered

        return True, None, checks, discovered

    def _diagnose_ldaps(self, params: LdapConnectParams) -> tuple[bool, str | None, list[CheckResult], str]:
        checks: list[CheckResult] = [
            CheckResult("dns", "not_tested"),
            CheckResult("tcp", "not_tested"),
            CheckResult("tls", "not_tested"),
            CheckResult("bind", "not_tested"),
            CheckResult("rootdse", "not_tested"),
            CheckResult("search", "not_tested"),
        ]
        host = (params.domain_controller or params.domain_dns_name or "").strip()
        if not host:
            checks[0].status = "failed"
            checks[0].detail = "Domain controller or DNS name is required."
            return False, checks[0].detail, checks, ""

        try:
            infos = socket.getaddrinfo(host, params.port, type=socket.SOCK_STREAM)
            checks[0].status = "pass"
            checks[0].detail = f"Resolved {host} → {infos[0][4][0]}"
        except OSError:
            checks[0].status = "failed"
            checks[0].detail = "Domain DNS resolution failed."
            return False, "Domain DNS resolution failed.", checks, ""

        try:
            with socket.create_connection((host, int(params.port)), timeout=params.timeout_seconds):
                pass
            checks[1].status = "pass"
            checks[1].detail = f"TCP {params.port} reachable."
        except OSError:
            checks[1].status = "failed"
            checks[1].detail = f"TCP {params.port} is not reachable."
            return False, checks[1].detail, checks, ""

        if not params.verify_tls:
            checks[2].status = "failed"
            checks[2].detail = "TLS certificate validation is required; insecure LDAP is not allowed."
            return False, checks[2].detail, checks, ""

        try:
            import ldap3
            from ldap3 import Connection, Server, Tls
            from ldap3.core.exceptions import LDAPException
        except ImportError:
            checks[2].status = "failed"
            checks[2].detail = "ldap3 package is not installed on the API host."
            return False, checks[2].detail, checks, ""

        tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT)
        server = Server(
            host,
            port=int(params.port),
            use_ssl=True,
            tls=tls,
            connect_timeout=params.timeout_seconds,
            get_info=ldap3.NONE,
        )
        try:
            conn = Connection(
                server,
                user=params.bind_username,
                password=params.bind_password,
                auto_bind=True,
                receive_timeout=params.timeout_seconds,
            )
            checks[2].status = "pass"
            checks[2].detail = "LDAPS TLS handshake succeeded with certificate validation."
            checks[3].status = "pass"
            checks[3].detail = "Service account bind succeeded."
        except LDAPException as exc:
            msg = str(exc)
            if "CERTIFICATE" in msg.upper() or "SSL" in msg.upper() or "TLS" in msg.upper():
                checks[2].status = "failed"
                checks[2].detail = "TLS handshake or certificate validation failed."
                return False, checks[2].detail, checks, ""
            checks[2].status = "pass"
            checks[2].detail = "TLS established."
            checks[3].status = "failed"
            checks[3].detail = "Service account bind failed."
            return False, "Service account bind failed.", checks, ""
        except ssl.SSLError:
            checks[2].status = "failed"
            checks[2].detail = "TLS handshake or certificate validation failed."
            return False, checks[2].detail, checks, ""

        discovered = (params.base_dn or "").strip()
        try:
            conn.search(
                search_base="",
                search_filter="(objectClass=*)",
                search_scope=ldap3.BASE,
                attributes=["defaultNamingContext", "rootDomainNamingContext"],
            )
            if conn.entries:
                entry = conn.entries[0]
                discovered = str(
                    getattr(entry, "defaultNamingContext", None)
                    or getattr(entry, "rootDomainNamingContext", None)
                    or discovered
                    or ""
                )
            checks[4].status = "pass"
            checks[4].detail = f"Base DN: {discovered or '(empty)'}"
            if not discovered:
                checks[4].status = "failed"
                checks[4].detail = "RootDSE did not return a naming context."
                conn.unbind()
                return False, checks[4].detail, checks, ""

            conn.search(
                search_base=discovered,
                search_filter="(objectClass=organizationalUnit)",
                search_scope=ldap3.LEVEL,
                attributes=["ou", "distinguishedName"],
                size_limit=5,
            )
            checks[5].status = "pass"
            checks[5].detail = f"Directory search succeeded ({len(conn.entries)} OU sample(s))."
            conn.unbind()
        except LDAPException:
            checks[4].status = checks[4].status if checks[4].status != "not_tested" else "failed"
            checks[5].status = "failed"
            checks[5].detail = "Directory base search failed."
            try:
                conn.unbind()
            except Exception:
                pass
            return False, "Directory base search failed.", checks, discovered

        return True, None, checks, discovered

    def _ado_connection(self):
        if os.name != "nt":
            raise LdapError("Windows Integrated LDAP requires Windows.")
        ensure_windows_com()
        import win32com.client

        conn = win32com.client.Dispatch("ADODB.Connection")
        conn.Provider = "ADsDSOObject"
        conn.Properties("ADSI Flag").Value = _WINDOWS_ADSI_FLAGS
        conn.Open("Active Directory Provider")
        return conn

    def _ado_search(
        self,
        host: str,
        base_dn: str,
        ldap_filter: str,
        attributes: list[str],
        *,
        scope: str = "subtree",
        limit: int = 500,
        timeout: int = 15,
    ) -> list[dict[str, Any]]:
        import win32com.client

        conn = self._ado_connection()
        try:
            cmd = win32com.client.Dispatch("ADODB.Command")
            cmd.ActiveConnection = conn
            try:
                cmd.Properties("Page Size").Value = min(max(limit, 1), 1000)
                cmd.Properties("Timeout").Value = max(5, int(timeout))
                cmd.Properties("Chase referrals").Value = False
            except Exception:
                pass
            attr_list = ",".join(attributes)
            cmd.CommandText = f"<LDAP://{host}/{base_dn}>;{ldap_filter};{attr_list};{scope}"
            rs, _ = cmd.Execute()
            rows: list[dict[str, Any]] = []
            while not rs.EOF and len(rows) < limit:
                item: dict[str, Any] = {}
                for i in range(rs.Fields.Count):
                    field = rs.Fields.Item(i)
                    item[str(field.Name)] = field.Value
                rows.append(item)
                rs.MoveNext()
            return rows
        finally:
            try:
                conn.Close()
            except Exception:
                pass

    def _windows_host_and_base(self, params: LdapConnectParams) -> tuple[str, str]:
        host, _ = resolve_directory_host(params)
        base = (params.base_dn or "").strip()
        if not base:
            # Prefer live RootDSE with signing/sealing.
            try:
                ensure_windows_com()
                import win32com.client

                dso = win32com.client.GetObject("LDAP:")
                root = dso.OpenDSObject(f"LDAP://{host}/RootDSE", "", "", _WINDOWS_ADSI_FLAGS)
                base = str(root.Get("defaultNamingContext") or "")
            except Exception:
                base = domain_dns_to_base_dn(params.domain_dns_name)
        if not base:
            raise LdapError("Base DN could not be determined from RootDSE.")
        return host, base

    def _connect_ldaps(self, params: LdapConnectParams):
        import ldap3
        from ldap3 import Connection, Server, Tls

        if not params.verify_tls:
            raise LdapError("TLS certificate validation is required.")
        host = (params.domain_controller or params.domain_dns_name).strip()
        tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT)
        server = Server(host, port=int(params.port), use_ssl=True, tls=tls, connect_timeout=params.timeout_seconds)
        conn = Connection(
            server,
            user=params.bind_username,
            password=params.bind_password,
            auto_bind=True,
            receive_timeout=params.timeout_seconds,
        )
        return ldap3, conn

    def list_ous(self, params: LdapConnectParams, *, parent_dn: str = "", search: str = "") -> list[AdOuNode]:
        mode = normalize_auth_mode(params.auth_mode)
        if mode == AUTH_WINDOWS_INTEGRATED:
            return self._list_ous_windows(params, parent_dn=parent_dn, search=search)
        return self._list_ous_ldaps(params, parent_dn=parent_dn, search=search)

    def _list_ous_windows(self, params: LdapConnectParams, *, parent_dn: str, search: str) -> list[AdOuNode]:
        host, default_base = self._windows_host_and_base(params)
        base = (parent_dn or default_base).strip()
        if not base:
            return []
        filt = "(objectClass=organizationalUnit)"
        needle = (search or "").strip()
        if needle:
            filt = f"(&(objectClass=organizationalUnit)(ou={escape_ldap_filter(needle)}*))"
        scope = "subtree" if needle else "onelevel"
        rows = self._ado_search(
            host, base, filt, ["ou", "distinguishedName"], scope=scope, limit=200, timeout=params.timeout_seconds
        )
        nodes: list[AdOuNode] = []
        for row in rows:
            dn = str(_ado_scalar(row.get("distinguishedName")) or "")
            name = str(_ado_scalar(row.get("ou")) or (dn.split(",")[0] if dn else ""))
            if dn:
                nodes.append(AdOuNode(name=name, distinguished_name=dn))
        return nodes

    def _list_ous_ldaps(self, params: LdapConnectParams, *, parent_dn: str, search: str) -> list[AdOuNode]:
        ldap3, conn = self._connect_ldaps(params)
        base = (parent_dn or params.base_dn or "").strip()
        if not base:
            conn.unbind()
            return []
        filt = "(objectClass=organizationalUnit)"
        needle = (search or "").strip()
        if needle:
            filt = f"(&(objectClass=organizationalUnit)(ou={escape_ldap_filter(needle)}*))"
        conn.search(
            search_base=base,
            search_filter=filt,
            search_scope=ldap3.LEVEL if not needle else ldap3.SUBTREE,
            attributes=["ou", "distinguishedName"],
            size_limit=200,
        )
        nodes: list[AdOuNode] = []
        for entry in conn.entries:
            dn = str(entry.distinguishedName)
            name = str(getattr(entry, "ou", None) or dn.split(",")[0])
            nodes.append(AdOuNode(name=name, distinguished_name=dn))
        conn.unbind()
        return nodes

    def search_users(
        self, params: LdapConnectParams, *, ou_dns: list[str], limit: int = 500
    ) -> list[AdUserRecord]:
        mode = normalize_auth_mode(params.auth_mode)
        if mode == AUTH_WINDOWS_INTEGRATED:
            return self._search_users_windows(params, ou_dns=ou_dns, limit=limit)
        return self._search_users_ldaps(params, ou_dns=ou_dns, limit=limit)

    def _search_users_windows(
        self, params: LdapConnectParams, *, ou_dns: list[str], limit: int
    ) -> list[AdUserRecord]:
        host, default_base = self._windows_host_and_base(params)
        bases = ou_dns or ([default_base] if default_base else [])
        out: list[AdUserRecord] = []
        attrs = [
            "objectGUID",
            "objectSid",
            "sAMAccountName",
            "userPrincipalName",
            "displayName",
            "mail",
            "department",
            "distinguishedName",
            "userAccountControl",
        ]
        for base in bases:
            if len(out) >= limit:
                break
            rows = self._ado_search(
                host,
                base,
                "(&(objectCategory=person)(objectClass=user))",
                attrs,
                scope="subtree",
                limit=max(1, limit - len(out)),
                timeout=params.timeout_seconds,
            )
            for row in rows:
                out.append(
                    AdUserRecord(
                        object_guid=_guid_to_str(_ado_scalar(row.get("objectGUID"))),
                        object_sid=_sid_to_str(_ado_scalar(row.get("objectSid"))),
                        sam_account_name=str(_ado_scalar(row.get("sAMAccountName")) or ""),
                        user_principal_name=str(_ado_scalar(row.get("userPrincipalName")) or ""),
                        display_name=str(_ado_scalar(row.get("displayName")) or ""),
                        mail=str(_ado_scalar(row.get("mail")) or ""),
                        department=str(_ado_scalar(row.get("department")) or ""),
                        distinguished_name=str(_ado_scalar(row.get("distinguishedName")) or ""),
                        account_enabled=_uac_enabled(_ado_scalar(row.get("userAccountControl"))),
                    )
                )
        return out[:limit]

    def _search_users_ldaps(
        self, params: LdapConnectParams, *, ou_dns: list[str], limit: int
    ) -> list[AdUserRecord]:
        ldap3, conn = self._connect_ldaps(params)
        out: list[AdUserRecord] = []
        bases = ou_dns or ([params.base_dn] if params.base_dn else [])
        for base in bases:
            if len(out) >= limit:
                break
            conn.search(
                search_base=base,
                search_filter="(&(objectCategory=person)(objectClass=user))",
                search_scope=ldap3.SUBTREE,
                attributes=[
                    "objectGUID",
                    "objectSid",
                    "sAMAccountName",
                    "userPrincipalName",
                    "displayName",
                    "mail",
                    "department",
                    "distinguishedName",
                    "userAccountControl",
                ],
                size_limit=max(1, limit - len(out)),
            )
            for entry in conn.entries:
                out.append(
                    AdUserRecord(
                        object_guid=_guid_to_str(entry.objectGUID.value if hasattr(entry, "objectGUID") else ""),
                        object_sid=_sid_to_str(entry.objectSid.value if hasattr(entry, "objectSid") else ""),
                        sam_account_name=str(getattr(entry, "sAMAccountName", "") or ""),
                        user_principal_name=str(getattr(entry, "userPrincipalName", "") or ""),
                        display_name=str(getattr(entry, "displayName", "") or ""),
                        mail=str(getattr(entry, "mail", "") or ""),
                        department=str(getattr(entry, "department", "") or ""),
                        distinguished_name=str(getattr(entry, "distinguishedName", "") or ""),
                        account_enabled=_uac_enabled(
                            entry.userAccountControl.value if hasattr(entry, "userAccountControl") else 0
                        ),
                    )
                )
        conn.unbind()
        return out[:limit]

    def search_groups(
        self, params: LdapConnectParams, *, ou_dns: list[str], limit: int = 500
    ) -> list[AdGroupRecord]:
        mode = normalize_auth_mode(params.auth_mode)
        if mode == AUTH_WINDOWS_INTEGRATED:
            return self._search_groups_windows(params, ou_dns=ou_dns, limit=limit)
        return self._search_groups_ldaps(params, ou_dns=ou_dns, limit=limit)

    def _search_groups_windows(
        self, params: LdapConnectParams, *, ou_dns: list[str], limit: int
    ) -> list[AdGroupRecord]:
        host, default_base = self._windows_host_and_base(params)
        bases = ou_dns or ([default_base] if default_base else [])
        out: list[AdGroupRecord] = []
        attrs = ["objectGUID", "objectSid", "cn", "distinguishedName", "groupType", "member"]
        for base in bases:
            if len(out) >= limit:
                break
            rows = self._ado_search(
                host,
                base,
                "(objectClass=group)",
                attrs,
                scope="subtree",
                limit=max(1, limit - len(out)),
                timeout=params.timeout_seconds,
            )
            for row in rows:
                gtype = 0
                try:
                    gtype = int(_ado_scalar(row.get("groupType")) or 0)
                except (TypeError, ValueError):
                    gtype = 0
                is_sec = bool(gtype & 0x80000000) if gtype else True
                members_raw = row.get("member")
                members: list[str] = []
                if members_raw is None:
                    members = []
                elif isinstance(members_raw, (list, tuple)):
                    members = [str(m) for m in members_raw if m]
                else:
                    members = [str(members_raw)]
                out.append(
                    AdGroupRecord(
                        object_guid=_guid_to_str(_ado_scalar(row.get("objectGUID"))),
                        object_sid=_sid_to_str(_ado_scalar(row.get("objectSid"))),
                        name=str(_ado_scalar(row.get("cn")) or ""),
                        distinguished_name=str(_ado_scalar(row.get("distinguishedName")) or ""),
                        is_security_group=is_sec,
                        member_guids=members,
                        member_sids=[],
                    )
                )
        return out[:limit]

    def _search_groups_ldaps(
        self, params: LdapConnectParams, *, ou_dns: list[str], limit: int
    ) -> list[AdGroupRecord]:
        ldap3, conn = self._connect_ldaps(params)
        out: list[AdGroupRecord] = []
        bases = ou_dns or ([params.base_dn] if params.base_dn else [])
        for base in bases:
            if len(out) >= limit:
                break
            conn.search(
                search_base=base,
                search_filter="(&(objectClass=group))",
                search_scope=ldap3.SUBTREE,
                attributes=["objectGUID", "objectSid", "cn", "distinguishedName", "groupType", "member"],
                size_limit=max(1, limit - len(out)),
            )
            for entry in conn.entries:
                gtype = 0
                try:
                    gtype = int(entry.groupType.value) if hasattr(entry, "groupType") else 0
                except (TypeError, ValueError):
                    gtype = 0
                is_sec = bool(gtype & 0x80000000) if gtype else True
                members = []
                if hasattr(entry, "member") and entry.member:
                    raw = entry.member.values if hasattr(entry.member, "values") else [entry.member.value]
                    members = [str(m) for m in raw if m]
                out.append(
                    AdGroupRecord(
                        object_guid=_guid_to_str(entry.objectGUID.value if hasattr(entry, "objectGUID") else ""),
                        object_sid=_sid_to_str(entry.objectSid.value if hasattr(entry, "objectSid") else ""),
                        name=str(getattr(entry, "cn", "") or ""),
                        distinguished_name=str(getattr(entry, "distinguishedName", "") or ""),
                        is_security_group=is_sec,
                        member_guids=members,
                        member_sids=[],
                    )
                )
        conn.unbind()
        return out[:limit]


_default_gateway: LdapGateway | None = None


def default_ldap_gateway() -> LdapGateway:
    global _default_gateway
    if _default_gateway is None:
        _default_gateway = RealLdapGateway()
    return _default_gateway
