"""AD workstation inventory, DNS/ICMP probes, employee association."""

from __future__ import annotations

from urllib.parse import quote

from qka_api.services import active_directory as ad
from qka_api.services.network_probe import DnsResult, PingResult
from tests.conftest import ALLOWED_USER, GOOGLE_ONLY_USER, auth_cookies
from tests.fake_ldap import FakeLdapGateway


class FakeProbe:
    def __init__(self) -> None:
        self.dns_map: dict[str, DnsResult] = {}
        self.ping_map: dict[str, PingResult] = {}
        self.default_dns = DnsResult(True, "10.0.0.50", "Resolved")
        self.default_ping = PingResult(True, "ICMP echo reply received.")

    def resolve_dns(self, hostname: str) -> DnsResult:
        key = (hostname or "").lower()
        return self.dns_map.get(key, self.default_dns)

    def icmp_ping(self, host: str, *, timeout_ms: int = 2000) -> PingResult:
        del timeout_ms
        return self.ping_map.get(host, self.default_ping)


def _admin(client):
    login = client.post("/api/v1/auth/login", json={"username": "super", "password": "123"})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get("qka_next_csrf")
    return {"X-CSRF-Token": csrf} if csrf else {}


def _install(client) -> tuple[FakeLdapGateway, FakeProbe]:
    gateway = FakeLdapGateway()
    probe = FakeProbe()
    client.app.state.ldap_gateway = gateway
    client.app.state.network_probe = probe
    ad._TEST_RATE.clear()
    return gateway, probe


def _ensure_ad_connection(client, headers) -> str:
    saved = client.post(
        "/api/v1/admin/active-directory/connections",
        headers=headers,
        json={
            "connectionName": "QEHC AD",
            "domainDnsName": "qehc.edu.sa",
            "domainController": "DC1.qehc.edu.sa",
            "authMode": "windows_integrated",
            "protocol": "windows_integrated",
        },
    )
    assert saved.status_code == 200, saved.text
    return saved.json()["id"]


def test_non_admin_cannot_manage_workstations(client):
    _install(client)
    response = client.get(
        "/api/v1/admin/workstations",
        cookies=auth_cookies(ALLOWED_USER),
    )
    assert response.status_code == 403


def test_sync_requires_ou_selection(client):
    _install(client)
    headers = _admin(client)
    conn_id = _ensure_ad_connection(client, headers)
    denied = client.post(
        "/api/v1/admin/workstations/sync",
        headers=headers,
        json={"connectionId": conn_id, "ouDns": [], "includeServers": False},
    )
    assert denied.status_code == 400
    assert "Organizational Unit" in denied.json()["detail"]


def test_scope_ou_dropdown_includes_nested_children(client):
    gateway, _probe = _install(client)
    headers = _admin(client)
    conn_id = _ensure_ad_connection(client, headers)
    from qka_api.ldap_client import AdComputerRecord

    # Nested under HO + sibling under Workstations root.
    gateway.computers.append(
        AdComputerRecord(
            object_guid="c5555555-5555-5555-5555-555555555555",
            object_sid="S-1-5-21-1-2-3-3005",
            computer_name="HO-PC01",
            dns_hostname="ho-pc01.qehc.edu.sa",
            distinguished_name="CN=HO-PC01,OU=HO,OU=QEHC,DC=qehc,DC=edu,DC=sa",
            operating_system="Windows 11 Pro",
            account_enabled=True,
            is_server_or_dc=False,
        )
    )
    gateway.computers.append(
        AdComputerRecord(
            object_guid="c6666666-6666-6666-6666-666666666666",
            object_sid="S-1-5-21-1-2-3-3006",
            computer_name="WSIT02",
            dns_hostname="wsit02.qehc.edu.sa",
            distinguished_name="CN=WSIT02,OU=WorkStations,OU=IT,OU=HO,OU=QEHC,DC=qehc,DC=edu,DC=sa",
            operating_system="Windows 11 Pro",
            account_enabled=True,
            is_server_or_dc=False,
        )
    )
    ws_ou = "OU=Workstations,DC=qehc,DC=edu,DC=sa"
    ho_ou = "OU=HO,OU=QEHC,DC=qehc,DC=edu,DC=sa"
    assert (
        client.post(
            "/api/v1/admin/workstations/sync",
            headers=headers,
            json={"connectionId": conn_id, "ouDns": [ws_ou]},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/admin/workstations/sync",
            headers=headers,
            json={"connectionId": conn_id, "ouDns": [ho_ou]},
        ).status_code
        == 200
    )

    # Default list is full inventory (no silent multi-OU banner filter).
    all_rows = client.get("/api/v1/admin/workstations", headers=headers)
    assert all_rows.status_code == 200
    all_names = {i["computerName"] for i in all_rows.json()["items"]}
    assert "HO-PC01" in all_names
    assert "WSIT02" in all_names
    assert "PILOT-PC" in all_names
    assert all_rows.json()["scopeOu"] is None

    # Dropdown scopeOu=HO includes HO and nested IT/WorkStations under HO.
    scoped = client.get(
        f"/api/v1/admin/workstations?scopeOu={quote(ho_ou)}",
        headers=headers,
    )
    assert scoped.status_code == 200, scoped.text
    names = {i["computerName"] for i in scoped.json()["items"]}
    assert names == {"HO-PC01", "WSIT02"}
    assert "PILOT-PC" not in names
    assert scoped.json()["scopeOu"] == ho_ou
    assert scoped.json()["summary"]["totalPresent"] == 2
    assert any(o["distinguishedName"] == ho_ou for o in scoped.json()["activeSyncOus"])

    # Legacy scopeOnly still OR-filters all selected sync OUs when requested.
    legacy = client.get("/api/v1/admin/workstations?scopeOnly=true", headers=headers)
    assert legacy.status_code == 200
    legacy_names = {i["computerName"] for i in legacy.json()["items"]}
    assert legacy_names == {"HO-PC01", "WSIT02"}


def test_display_ou_filter_multi_select_and_ou_tree(client):
    gateway, _probe = _install(client)
    headers = _admin(client)
    conn_id = _ensure_ad_connection(client, headers)
    from qka_api.ldap_client import AdComputerRecord

    gateway.computers.append(
        AdComputerRecord(
            object_guid="c5555555-5555-5555-5555-555555555555",
            object_sid="S-1-5-21-1-2-3-3005",
            computer_name="HO-PC01",
            dns_hostname="ho-pc01.qehc.edu.sa",
            distinguished_name="CN=HO-PC01,OU=HO,OU=QEHC,DC=qehc,DC=edu,DC=sa",
            operating_system="Windows 11 Pro",
            account_enabled=True,
            is_server_or_dc=False,
        )
    )
    gateway.computers.append(
        AdComputerRecord(
            object_guid="c6666666-6666-6666-6666-666666666666",
            object_sid="S-1-5-21-1-2-3-3006",
            computer_name="WSIT02",
            dns_hostname="wsit02.qehc.edu.sa",
            distinguished_name="CN=WSIT02,OU=WorkStations,OU=IT,OU=HO,OU=QEHC,DC=qehc,DC=edu,DC=sa",
            operating_system="Windows 11 Pro",
            account_enabled=True,
            is_server_or_dc=False,
        )
    )
    ho_ou = "OU=HO,OU=QEHC,DC=qehc,DC=edu,DC=sa"
    assert (
        client.post(
            "/api/v1/admin/workstations/sync",
            headers=headers,
            json={"connectionId": conn_id, "ouDns": [ho_ou]},
        ).status_code
        == 200
    )

    all_rows = client.get("/api/v1/admin/workstations", headers=headers)
    assert all_rows.status_code == 200
    body = all_rows.json()
    all_names = {i["computerName"] for i in body["items"]}
    assert "HO-PC01" in all_names
    assert "WSIT02" in all_names
    assert isinstance(body.get("ouTree"), list)
    assert body.get("ouTree"), "ouTree should be populated from inventory metadata"
    assert body.get("includeChildren") is True
    assert body.get("filterOuDns") == []
    # Sync scope metadata is separate from display filter.
    assert any(o["distinguishedName"] == ho_ou for o in body["activeSyncOus"])
    sync_before = [o["distinguishedName"] for o in body["activeSyncOus"]]

    filtered = client.get(
        f"/api/v1/admin/workstations?filterOuDns={quote(ho_ou)}&includeChildren=true",
        headers=headers,
    )
    assert filtered.status_code == 200, filtered.text
    fbody = filtered.json()
    assert {i["computerName"] for i in fbody["items"]} == {"HO-PC01", "WSIT02"}
    assert fbody["filterOuDns"] == [ho_ou]
    assert fbody["summary"]["totalPresent"] == 2
    # Display filter must not mutate saved sync OUs.
    assert [o["distinguishedName"] for o in fbody["activeSyncOus"]] == sync_before

    exact = client.get(
        f"/api/v1/admin/workstations?filterOuDns={quote(ho_ou)}&includeChildren=false",
        headers=headers,
    )
    assert exact.status_code == 200
    exact_names = {i["computerName"] for i in exact.json()["items"]}
    assert exact_names == {"HO-PC01"}  # WSIT02 is nested under HO/IT/WorkStations

    unknown = client.get(
        "/api/v1/admin/workstations?filterOuDns=OU=NotSelected,DC=qehc,DC=edu,DC=sa",
        headers=headers,
    )
    assert unknown.status_code == 200
    assert unknown.json()["total"] == 0
    assert unknown.json()["filterOuDns"] == ["OU=NotSelected,DC=qehc,DC=edu,DC=sa"]


def test_sync_ou_options_supports_nested_browse(client):
    _install(client)
    headers = _admin(client)
    conn_id = _ensure_ad_connection(client, headers)
    root = client.get(
        f"/api/v1/admin/workstations/sync-ou-options?connectionId={conn_id}",
        headers=headers,
    )
    assert root.status_code == 200, root.text
    names = {i["name"] for i in root.json()["items"]}
    assert "QEHC" in names
    assert "Suspend WorkStations" not in names
    nested = client.get(
        f"/api/v1/admin/workstations/sync-ou-options?connectionId={conn_id}"
        f"&parentDn={quote('OU=QEHC,DC=qehc,DC=edu,DC=sa')}",
        headers=headers,
    )
    assert nested.status_code == 200, nested.text
    child_names = {i["name"] for i in nested.json()["items"]}
    assert "Suspend WorkStations" in child_names


def test_sync_excludes_servers_and_preserves_missing(client):
    gateway, _probe = _install(client)
    headers = _admin(client)
    conn_id = _ensure_ad_connection(client, headers)
    ou = "OU=Workstations,DC=qehc,DC=edu,DC=sa"

    options = client.get(
        f"/api/v1/admin/workstations/sync-ou-options?connectionId={conn_id}",
        headers=headers,
    )
    assert options.status_code == 200, options.text
    assert any(i["distinguishedName"] == ou for i in options.json()["items"])

    sync = client.post(
        "/api/v1/admin/workstations/sync",
        headers=headers,
        json={"connectionId": conn_id, "ouDns": [ou], "includeServers": False},
    )
    assert sync.status_code == 200, sync.text
    body = sync.json()
    assert body["discovered"] == 3
    assert body["created"] == 3
    assert body["present"] == 3
    assert ou in body["syncedOus"]

    listed = client.get("/api/v1/admin/workstations", headers=headers)
    assert listed.status_code == 200
    names = {i["computerName"] for i in listed.json()["items"]}
    assert "PILOT-PC" in names
    assert "QEHC-DC01" not in names
    assert listed.json()["summary"]["totalPresent"] == 3

    # Remove one workstation from directory and resync — mark missing, do not delete.
    gateway.computers = [c for c in gateway.computers if c.computer_name != "FINANCE-01"]
    again = client.post(
        "/api/v1/admin/workstations/sync",
        headers=headers,
        json={"connectionId": conn_id, "ouDns": [ou]},
    )
    assert again.status_code == 200
    assert again.json()["missingMarked"] == 1
    assert again.json()["present"] == 2

    with_missing = client.get(
        "/api/v1/admin/workstations?includeMissing=true&search=FINANCE",
        headers=headers,
    )
    assert with_missing.status_code == 200
    finance = [i for i in with_missing.json()["items"] if i["computerName"] == "FINANCE-01"]
    assert finance
    assert finance[0]["isPresent"] is False


def test_dns_failure_and_icmp_no_reply(client):
    _gateway, probe = _install(client)
    headers = _admin(client)
    conn_id = _ensure_ad_connection(client, headers)
    ou = "OU=Workstations,DC=qehc,DC=edu,DC=sa"
    client.post(
        "/api/v1/admin/workstations/sync",
        headers=headers,
        json={"connectionId": conn_id, "ouDns": [ou]},
    )
    items = client.get("/api/v1/admin/workstations", headers=headers).json()["items"]
    pilot = next(i for i in items if i["computerName"] == "PILOT-PC")
    finance = next(i for i in items if i["computerName"] == "FINANCE-01")

    probe.dns_map["pilot-pc.qehc.edu.sa"] = DnsResult(False, "", "DNS resolution failed: nxdomain")
    dns_fail = client.post(
        f"/api/v1/admin/workstations/{pilot['id']}/ping",
        headers=headers,
        json={"timeoutMs": 500},
    )
    assert dns_fail.status_code == 200, dns_fail.text
    assert dns_fail.json()["reachabilityStatus"] == "dns_failed"
    assert dns_fail.json()["ipAddress"] == ""

    probe.dns_map["finance-01.qehc.edu.sa"] = DnsResult(True, "10.0.0.61", "Resolved")
    probe.ping_map["10.0.0.61"] = PingResult(
        False,
        "No ICMP reply. This may mean the host is offline, firewalled, or ICMP is blocked.",
    )
    no_reply = client.post(
        f"/api/v1/admin/workstations/{finance['id']}/ping",
        headers=headers,
        json={},
    )
    assert no_reply.status_code == 200
    assert no_reply.json()["reachabilityStatus"] == "no_reply"
    assert no_reply.json()["ipAddress"] == "10.0.0.61"
    assert "powered off" in (no_reply.json().get("reachabilityNote") or "").lower()


def test_bulk_ping_only_selected(client):
    _gateway, probe = _install(client)
    headers = _admin(client)
    conn_id = _ensure_ad_connection(client, headers)
    ou = "OU=Workstations,DC=qehc,DC=edu,DC=sa"
    client.post(
        "/api/v1/admin/workstations/sync",
        headers=headers,
        json={"connectionId": conn_id, "ouDns": [ou]},
    )
    items = client.get("/api/v1/admin/workstations", headers=headers).json()["items"]
    ids = [i["id"] for i in items[:2]]
    probe.default_ping = PingResult(True, "ICMP echo reply received.")

    bulk = client.post(
        "/api/v1/admin/workstations/ping-bulk",
        headers=headers,
        json={"workstationIds": ids, "timeoutMs": 800, "concurrency": 2},
    )
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["processed"] == 2
    assert all(r["reachabilityStatus"] == "reachable" for r in bulk.json()["results"])
    assert "powered off" in bulk.json()["note"].lower()

    # Refuse arbitrary / non-inventory IDs (no unrestricted scans).
    bad = client.post(
        "/api/v1/admin/workstations/ping-bulk",
        headers=headers,
        json={"workstationIds": ["00000000-0000-0000-0000-000000000099"]},
    )
    assert bad.status_code == 400


def test_employee_association_requires_ad_user(client):
    _install(client)
    headers = _admin(client)
    conn_id = _ensure_ad_connection(client, headers)
    ou = "OU=Workstations,DC=qehc,DC=edu,DC=sa"
    client.post(
        "/api/v1/admin/workstations/sync",
        headers=headers,
        json={"connectionId": conn_id, "ouDns": [ou]},
    )
    pilot = next(
        i
        for i in client.get("/api/v1/admin/workstations", headers=headers).json()["items"]
        if i["computerName"] == "PILOT-PC"
    )
    assert pilot["agentStatus"] == "not_registered"
    assert pilot["agentStatusLabel"] == "Not registered"
    assert pilot["deployAgent"]["enabled"] is False

    denied = client.put(
        f"/api/v1/admin/workstations/{pilot['id']}/employee",
        headers=headers,
        json={"qkaUserId": str(GOOGLE_ONLY_USER)},
    )
    assert denied.status_code == 400
    assert "AD-linked" in denied.json()["detail"]

    # AD-enrolled employees often have login_enabled=False until Google/SSO; assignment must still work.
    from qka_api.db import create_session_factory
    from qka_shared.config import get_settings
    from qka_shared.models import QkaUser

    get_settings.cache_clear()
    engine, factory = create_session_factory(get_settings())
    try:
        with factory() as session:
            user = session.get(QkaUser, ALLOWED_USER)
            assert user is not None
            user.login_enabled = False
            session.commit()
    finally:
        engine.dispose()
        get_settings.cache_clear()

    ok = client.put(
        f"/api/v1/admin/workstations/{pilot['id']}/employee",
        headers=headers,
        json={"qkaUserId": str(ALLOWED_USER)},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["assignedEmployeeId"] == str(ALLOWED_USER)
    assert ok.json()["assignedEmployeeName"]

    cleared = client.put(
        f"/api/v1/admin/workstations/{pilot['id']}/employee",
        headers=headers,
        json={"qkaUserId": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["assignedEmployeeId"] is None
