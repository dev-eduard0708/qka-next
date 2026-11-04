"""Mock LDAP gateway for Active Directory tests (no live directory)."""

from __future__ import annotations

from qka_api.ldap_client import (
    AUTH_LDAPS,
    AUTH_WINDOWS_INTEGRATED,
    AdGroupRecord,
    AdOuNode,
    AdUserRecord,
    CheckResult,
    LdapConnectParams,
    normalize_auth_mode,
)


class FakeLdapGateway:
    def __init__(self) -> None:
        self.fail_tls = False
        self.fail_bind = False
        self.fail_dns = False
        self.fail_kerberos = False
        self.users = [
            AdUserRecord(
                object_guid="11111111-1111-1111-1111-111111111111",
                object_sid="S-1-5-21-1-2-3-1001",
                sam_account_name="employee1",
                user_principal_name="employee1@qehc.edu.sa",
                display_name="Employee One",
                mail="employee1@qehc.edu.sa",
                department="IT",
                distinguished_name="CN=Employee One,OU=Users,DC=qehc,DC=edu,DC=sa",
                account_enabled=True,
            ),
            AdUserRecord(
                object_guid="22222222-2222-2222-2222-222222222222",
                object_sid="S-1-5-21-1-2-3-1002",
                sam_account_name="orphan",
                user_principal_name="orphan@other.local",
                display_name="Orphan",
                mail="",
                department="Finance",
                distinguished_name="CN=Orphan,OU=Users,DC=qehc,DC=edu,DC=sa",
                account_enabled=False,
            ),
        ]
        self.groups = [
            AdGroupRecord(
                object_guid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                object_sid="S-1-5-21-1-2-3-2001",
                name="IT-Staff",
                distinguished_name="CN=IT-Staff,OU=Groups,DC=qehc,DC=edu,DC=sa",
                is_security_group=True,
                member_guids=["CN=Employee One,OU=Users,DC=qehc,DC=edu,DC=sa"],
            )
        ]
        self.ous = [
            AdOuNode(name="Users", distinguished_name="OU=Users,DC=qehc,DC=edu,DC=sa"),
            AdOuNode(name="Groups", distinguished_name="OU=Groups,DC=qehc,DC=edu,DC=sa"),
        ]

    def diagnose(self, params: LdapConnectParams):
        mode = normalize_auth_mode(params.auth_mode)
        if mode == AUTH_WINDOWS_INTEGRATED:
            return self._diagnose_windows(params)
        return self._diagnose_ldaps(params)

    def _diagnose_windows(self, params: LdapConnectParams):
        checks = [
            CheckResult("dns", "not_tested"),
            CheckResult("tcp", "not_tested"),
            CheckResult("identity", "not_tested"),
            CheckResult("kerberos", "not_tested"),
            CheckResult("signing", "not_tested"),
            CheckResult("sealing", "not_tested"),
            CheckResult("rootdse", "not_tested"),
            CheckResult("search", "not_tested"),
        ]
        if self.fail_dns:
            checks[0].status = "failed"
            checks[0].detail = "Domain DNS resolution failed."
            return False, checks[0].detail, checks, ""
        checks[0].status = "pass"
        checks[0].detail = "Resolved"
        checks[1].status = "pass"
        checks[1].detail = "TCP ok"
        checks[2].status = "pass"
        checks[2].detail = "Service identity: QEHC\\qka-reader"
        if self.fail_kerberos or self.fail_bind:
            checks[3].status = "failed"
            checks[3].detail = "Kerberos/SSPI authentication failed for the Windows service identity."
            checks[4].status = "failed"
            checks[4].detail = "LDAP signing not negotiated — authentication did not succeed."
            checks[5].status = "failed"
            checks[5].detail = "LDAP sealing not negotiated — authentication did not succeed."
            return False, checks[3].detail, checks, ""
        checks[3].status = "pass"
        checks[3].detail = "Kerberos/SSPI bind succeeded"
        checks[4].status = "pass"
        checks[4].detail = "LDAP signing negotiated"
        checks[5].status = "pass"
        checks[5].detail = "LDAP sealing negotiated"
        base = params.base_dn or "DC=qehc,DC=edu,DC=sa"
        checks[6].status = "pass"
        checks[6].detail = f"Base DN: {base}"
        checks[7].status = "pass"
        checks[7].detail = "Search ok"
        return True, None, checks, base

    def _diagnose_ldaps(self, params: LdapConnectParams):
        checks = [
            CheckResult("dns", "not_tested"),
            CheckResult("tcp", "not_tested"),
            CheckResult("tls", "not_tested"),
            CheckResult("bind", "not_tested"),
            CheckResult("rootdse", "not_tested"),
            CheckResult("search", "not_tested"),
        ]
        if self.fail_dns:
            checks[0].status = "failed"
            checks[0].detail = "Domain DNS resolution failed."
            return False, checks[0].detail, checks, ""
        checks[0].status = "pass"
        checks[0].detail = "Resolved"
        checks[1].status = "pass"
        checks[1].detail = "TCP ok"
        if not params.verify_tls:
            checks[2].status = "failed"
            checks[2].detail = "TLS required"
            return False, checks[2].detail, checks, ""
        if self.fail_tls:
            checks[2].status = "failed"
            checks[2].detail = "TLS handshake or certificate validation failed."
            return False, checks[2].detail, checks, ""
        checks[2].status = "pass"
        checks[2].detail = "TLS ok"
        if self.fail_bind or not params.bind_password:
            checks[3].status = "failed"
            checks[3].detail = "Service account bind failed."
            return False, checks[3].detail, checks, ""
        checks[3].status = "pass"
        checks[3].detail = "Bind ok"
        base = params.base_dn or "DC=qehc,DC=edu,DC=sa"
        checks[4].status = "pass"
        checks[4].detail = f"Base DN: {base}"
        checks[5].status = "pass"
        checks[5].detail = "Search ok"
        return True, None, checks, base

    def list_ous(self, params: LdapConnectParams, *, parent_dn: str = "", search: str = ""):
        needle = (search or "").lower()
        if needle:
            return [o for o in self.ous if needle in o.name.lower()]
        return list(self.ous)

    def search_users(self, params: LdapConnectParams, *, ou_dns: list[str], limit: int = 500):
        return list(self.users)[:limit]

    def search_groups(self, params: LdapConnectParams, *, ou_dns: list[str], limit: int = 500):
        return list(self.groups)[:limit]
