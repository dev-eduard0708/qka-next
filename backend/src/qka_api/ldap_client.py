"""LDAPS client for QKA Active Directory integration.

Certificate validation is required. Plain LDAP is not supported.
Passwords are never logged. Gateway is injectable for tests.
"""

from __future__ import annotations

import re
import socket
import ssl
from dataclasses import dataclass, field
from typing import Any, Protocol


class LdapError(Exception):
    def __init__(self, message: str, *, diagnostics: list[str] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


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
    port: int = 636
    bind_username: str = ""
    bind_password: str = ""
    base_dn: str = ""
    timeout_seconds: int = 15
    verify_tls: bool = True


_LDAP_SPECIAL = re.compile(r"([\\*()+\x00])")


def escape_ldap_filter(value: str) -> str:
    """RFC 4515 filter escaping — never interpolate untrusted input raw."""
    return _LDAP_SPECIAL.sub(lambda m: "\\%02x" % ord(m.group(1)), value or "")


def escape_ldap_dn(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace(",", "\\,")


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
    if isinstance(raw, bytes) and len(raw) == 16:
        import uuid as _uuid

        return str(_uuid.UUID(bytes_le=raw))
    return str(raw)


def _sid_to_str(raw: Any) -> str:
    if raw is None:
        return ""
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


class RealLdapGateway:
    """Production LDAPS gateway using ldap3 with CERT_REQUIRED."""

    def diagnose(self, params: LdapConnectParams) -> tuple[bool, str | None, list[CheckResult], str]:
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

        # 1. DNS
        try:
            infos = socket.getaddrinfo(host, params.port, type=socket.SOCK_STREAM)
            checks[0].status = "pass"
            checks[0].detail = f"Resolved {host} → {infos[0][4][0]}"
        except OSError as exc:
            checks[0].status = "failed"
            checks[0].detail = "Domain DNS resolution failed."
            return False, "Domain DNS resolution failed.", checks, ""

        # 2. TCP
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
        # 3. TLS + 4. Bind
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
            # Never echo password; sanitize common messages.
            safe = "LDAPS TLS or bind failed."
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
            # 5. RootDSE
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

            # 6. Read-only search
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

    def _connect(self, params: LdapConnectParams):
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
        ldap3, conn = self._connect(params)
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
        ldap3, conn = self._connect(params)
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
        ldap3, conn = self._connect(params)
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
                        member_guids=[],  # resolved later via DN map if needed
                        member_sids=[],
                    )
                )
                # Store member DNs temporarily in member_guids field via encoding — use member_sids unused
                out[-1].member_guids = members  # temporarily DNs; sync layer resolves
        conn.unbind()
        return out[:limit]


_default_gateway: LdapGateway | None = None


def default_ldap_gateway() -> LdapGateway:
    global _default_gateway
    if _default_gateway is None:
        _default_gateway = RealLdapGateway()
    return _default_gateway
