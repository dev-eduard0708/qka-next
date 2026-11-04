from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from qka_api.services import active_directory as ad
from qka_shared.allowlist import LINKED, PROVIDER_AD, PROVIDER_GOOGLE
from qka_shared.models import AllowedEmail, ChatAccess, ExternalIdentity, GoogleWorkspaceAccount, QkaUser
from tests.fake_ldap import FakeLdapGateway
from qka_api.services import user_directory as directory


def _admin(client):
    login = client.post("/api/v1/auth/login", json={"username": "super", "password": "123"})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get("qka_next_csrf")
    return {"X-CSRF-Token": csrf} if csrf else {}


def _install_fake(client) -> FakeLdapGateway:
    gateway = FakeLdapGateway()
    client.app.state.ldap_gateway = gateway
    ad._TEST_RATE.clear()
    return gateway


def _ldaps_body(**extra):
    body = {
        "connectionName": "Corp AD",
        "domainDnsName": "ad.example.local",
        "domainController": "dc01.ad.example.local",
        "authMode": "ldaps",
        "protocol": "ldaps",
        "port": 636,
        "bindUsername": "DOMAIN\\reader",
        "bindPassword": "secret-value",
        "verifyTls": True,
    }
    body.update(extra)
    return body


def _windows_body(**extra):
    body = {
        "connectionName": "QEHC AD",
        "domainDnsName": "qehc.edu.sa",
        "domainController": "DC1.qehc.edu.sa",
        "authMode": "windows_integrated",
        "protocol": "windows_integrated",
    }
    body.update(extra)
    return body


def test_non_admin_cannot_administer_ad(client):
    from tests.conftest import ALLOWED_USER, auth_cookies

    _install_fake(client)
    response = client.get(
        "/api/v1/admin/active-directory/connections",
        cookies=auth_cookies(ALLOWED_USER),
    )
    assert response.status_code == 403


def test_connection_validation_tls_and_bind(client):
    gateway = _install_fake(client)
    headers = _admin(client)

    gateway.fail_tls = True
    failed = client.post(
        "/api/v1/admin/active-directory/connections/test",
        headers=headers,
        json=_ldaps_body(bindPassword="secret"),
    )
    assert failed.status_code == 200, failed.text
    body = failed.json()
    assert body["succeeded"] is False
    assert any(c["name"] == "tls" and c["status"] == "failed" for c in body["checks"])
    assert "password" not in failed.text.lower() or "bindPassword" not in str(body)

    gateway.fail_tls = False
    gateway.fail_bind = True
    ad._TEST_RATE.clear()
    bind_fail = client.post(
        "/api/v1/admin/active-directory/connections/test",
        headers=headers,
        json=_ldaps_body(bindPassword="bad"),
    )
    assert bind_fail.json()["succeeded"] is False
    assert any(c["name"] == "bind" and c["status"] == "failed" for c in bind_fail.json()["checks"])


def test_windows_integrated_test_and_save(client):
    gateway = _install_fake(client)
    headers = _admin(client)

    gateway.fail_kerberos = True
    failed = client.post(
        "/api/v1/admin/active-directory/connections/test",
        headers=headers,
        json=_windows_body(),
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["succeeded"] is False
    assert failed.json()["authMode"] == "windows_integrated"
    assert any(c["name"] == "kerberos" and c["status"] == "failed" for c in failed.json()["checks"])
    # Signing/sealing must not claim negotiation success when Kerberos failed.
    assert any(c["name"] == "signing" and c["status"] == "failed" for c in failed.json()["checks"])
    assert any(c["name"] == "sealing" and c["status"] == "failed" for c in failed.json()["checks"])

    gateway.fail_kerberos = False
    ad._TEST_RATE.clear()
    ok = client.post(
        "/api/v1/admin/active-directory/connections/test",
        headers=headers,
        json=_windows_body(),
    )
    assert ok.status_code == 200, ok.text
    payload = ok.json()
    assert payload["succeeded"] is True
    assert payload["discoveredBaseDn"]
    assert payload["serviceIdentity"] is not None
    assert any(c["name"] == "signing" and c["status"] == "pass" for c in payload["checks"])
    assert any(c["name"] == "sealing" and c["status"] == "pass" for c in payload["checks"])

    ad._TEST_RATE.clear()
    created = client.post(
        "/api/v1/admin/active-directory/connections",
        headers=headers,
        json=_windows_body(),
    )
    assert created.status_code == 200, created.text
    row = created.json()
    assert row["authMode"] == "windows_integrated"
    assert row["protocol"] == "windows_integrated"
    assert row["port"] == 389
    assert row["bindSecretConfigured"] is False
    assert row["baseDn"]
    assert "password" not in created.text.lower() or "secret" not in created.text.lower()


def test_save_requires_successful_test_and_hides_secret(client):
    gateway = _install_fake(client)
    headers = _admin(client)
    gateway.fail_bind = True
    denied = client.post(
        "/api/v1/admin/active-directory/connections",
        headers=headers,
        json=_ldaps_body(),
    )
    assert denied.status_code == 400

    gateway.fail_bind = False
    ad._TEST_RATE.clear()
    created = client.post(
        "/api/v1/admin/active-directory/connections",
        headers=headers,
        json=_ldaps_body(),
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["bindSecretConfigured"] is True
    assert "bindPassword" not in payload
    assert "secret-value" not in created.text
    assert payload["protocol"] == "ldaps"
    assert payload["authMode"] == "ldaps"
    assert payload["connectionStatus"] == ad.STATUS_CONNECTED


def test_ou_user_group_sync_and_identity_link(client):
    gateway = _install_fake(client)
    headers = _admin(client)
    created = client.post(
        "/api/v1/admin/active-directory/connections",
        headers=headers,
        json=_windows_body(),
    )
    assert created.status_code == 200, created.text
    cid = created.json()["id"]

    ous = client.get(f"/api/v1/admin/active-directory/connections/{cid}/ous", headers=headers)
    assert ous.status_code == 200
    assert len(ous.json()["items"]) >= 1

    scope = client.put(
        f"/api/v1/admin/active-directory/connections/{cid}/ou-scope",
        headers=headers,
        json={
            "items": [
                {"name": "Users", "distinguishedName": "OU=Users,DC=qehc,DC=edu,DC=sa", "selected": True}
            ]
        },
    )
    assert scope.status_code == 200

    # Seed a Google identity matching employee1 for pending candidate.
    with client.app.state.session_factory() as session:
        user = session.execute(select(QkaUser).where(QkaUser.is_super_admin.is_(False))).scalars().first()
        assert user is not None
        target_id = user.id
        now = datetime.now(timezone.utc)
        identity = ExternalIdentity(
            id=uuid.uuid4(),
            qka_user_id=target_id,
            provider=PROVIDER_GOOGLE,
            stable_external_id="google-sub-employee1",
            primary_email="employee1@qehc.edu.sa",
            account_status=0,
            link_status=LINKED,
            directory_presence=0,
            created_at=now,
            updated_at=now,
        )
        session.add(identity)
        session.flush()
        session.add(
            GoogleWorkspaceAccount(
                id=uuid.uuid4(),
                external_identity_id=identity.id,
                google_user_id="google-sub-employee1",
                primary_email="employee1@qehc.edu.sa",
            )
        )
        google_before = session.scalar(
            select(ExternalIdentity).where(ExternalIdentity.provider == PROVIDER_GOOGLE)
        )
        assert google_before is not None
        session.commit()

    synced = client.post(f"/api/v1/admin/active-directory/connections/{cid}/sync", headers=headers)
    assert synced.status_code == 200, synced.text
    job = synced.json()
    assert job["status"] == ad.JOB_COMPLETED
    assert job["usersDiscovered"] >= 2
    assert job["groupsDiscovered"] >= 1
    assert job["enumerationComplete"] is True

    users = client.get(f"/api/v1/admin/active-directory/connections/{cid}/users", headers=headers)
    assert users.status_code == 200
    items = users.json()["items"]
    pending = next(i for i in items if i["samAccountName"] == "employee1")
    assert pending["mappingStatus"] == ad.MAP_PENDING
    assert pending["objectSid"].startswith("S-")

    linked = client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/users/{pending['id']}/link",
        headers=headers,
        json={"qkaUserId": str(target_id)},
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["mappingStatus"] == ad.MAP_LINKED

    with client.app.state.session_factory() as session:
        ad_idents = session.execute(
            select(ExternalIdentity).where(ExternalIdentity.provider == PROVIDER_AD)
        ).scalars().all()
        assert any(i.stable_external_id == "S-1-5-21-1-2-3-1001" and i.link_status == LINKED for i in ad_idents)
        google_count = session.scalar(
            select(ExternalIdentity).where(ExternalIdentity.provider == PROVIDER_GOOGLE)
        )
        assert google_count is not None

    # Conflict: cannot link same AD user to another QKA user
    other = client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/users/{pending['id']}/link",
        headers=headers,
        json={"qkaUserId": str(uuid.uuid4())},
    )
    assert other.status_code in {400, 409}

    overview = client.get(f"/api/v1/admin/active-directory/overview?connectionId={cid}", headers=headers)
    assert overview.status_code == 200
    assert overview.json()["linkedIdentities"] >= 1
    assert overview.json()["usersDiscovered"] >= 2

    # Incomplete sync protection: second sync still keeps linked identity
    ad._TEST_RATE.clear()
    again = client.post(f"/api/v1/admin/active-directory/connections/{cid}/resync", headers=headers)
    assert again.status_code == 200
    with client.app.state.session_factory() as session:
        still = session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == PROVIDER_AD,
                ExternalIdentity.stable_external_id == "S-1-5-21-1-2-3-1001",
                ExternalIdentity.link_status == LINKED,
            )
        ).scalars().first()
        assert still is not None


def test_reject_insecure_ldap_protocol(client):
    _install_fake(client)
    headers = _admin(client)
    response = client.post(
        "/api/v1/admin/active-directory/connections/test",
        headers=headers,
        json={
            "domainDnsName": "ad.example.local",
            "domainController": "dc01.ad.example.local",
            "protocol": "ldap",
            "port": 389,
            "bindUsername": "u",
            "bindPassword": "p",
            "verifyTls": False,
        },
    )
    assert response.status_code == 400


def _sync_connection(client, headers) -> str:
    created = client.post(
        "/api/v1/admin/active-directory/connections",
        headers=headers,
        json=_windows_body(),
    )
    assert created.status_code == 200, created.text
    cid = created.json()["id"]
    scope = client.put(
        f"/api/v1/admin/active-directory/connections/{cid}/ou-scope",
        headers=headers,
        json={
            "items": [
                {"name": "Users", "distinguishedName": "OU=Users,DC=qehc,DC=edu,DC=sa", "selected": True}
            ]
        },
    )
    assert scope.status_code == 200
    synced = client.post(f"/api/v1/admin/active-directory/connections/{cid}/sync", headers=headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["status"] == ad.JOB_COMPLETED
    return cid


def test_ad_discovery_eligibility_and_enrollment(client):
    from qka_shared.models import AdAuditEvent, AdDirectoryUser, ChatAccess, ExternalIdentity as Ext
    from tests.conftest import ALLOWED_USER, auth_cookies

    _install_fake(client)
    headers = _admin(client)
    cid = _sync_connection(client, headers)

    users = client.get(f"/api/v1/admin/active-directory/connections/{cid}/users", headers=headers)
    assert users.status_code == 200
    items = users.json()["items"]
    assert all(item["qkaEligible"] is False for item in items)
    # Discovery must not create QKA application users for AD SIDs.
    with client.app.state.session_factory() as session:
        before_ad = session.execute(
            select(Ext).where(Ext.provider == PROVIDER_AD, Ext.link_status == LINKED)
        ).scalars().all()
        # Seed fixtures may already have AD identities for pilot users; none from this sync GUID set.
        synced_sids = {i["objectSid"] for i in items}
        assert not any(row.stable_external_id in synced_sids for row in before_ad)

    employee1 = next(i for i in items if i["samAccountName"] == "employee1")
    noemail = next(i for i in items if i["samAccountName"] == "noemail")
    # Disabled accounts are archived by default and omitted from the active list.
    archived_items = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?archived=true&pageSize=50",
        headers=headers,
    ).json()["items"]
    orphan = next(i for i in archived_items if i["samAccountName"] == "orphan")

    # Non-admin cannot change eligibility.
    denied = client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/eligibility",
        cookies=auth_cookies(ALLOWED_USER),
        json={"directoryUserIds": [employee1["id"]], "eligible": True},
    )
    assert denied.status_code == 403

    # Disabled AD account cannot be newly marked eligible.
    skip = client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/eligibility",
        headers=headers,
        json={"directoryUserIds": [orphan["id"]], "eligible": True},
    )
    assert skip.status_code == 200
    assert skip.json()["updated"] == 0
    assert skip.json()["skipped"]

    mark = client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/eligibility",
        headers=headers,
        json={"directoryUserIds": [employee1["id"], noemail["id"]], "eligible": True},
    )
    assert mark.status_code == 200, mark.text
    assert mark.json()["updated"] == 2
    with client.app.state.session_factory() as session:
        audits = session.execute(
            select(AdAuditEvent).where(AdAuditEvent.action == "eligibility_enable")
        ).scalars().all()
        assert len(audits) >= 2

    # Eligibility persists across resync.
    ad._TEST_RATE.clear()
    resync = client.post(f"/api/v1/admin/active-directory/connections/{cid}/resync", headers=headers)
    assert resync.status_code == 200
    after = client.get(f"/api/v1/admin/active-directory/connections/{cid}/users", headers=headers)
    by_sam = {i["samAccountName"]: i for i in after.json()["items"]}
    assert by_sam["employee1"]["qkaEligible"] is True
    assert by_sam["noemail"]["qkaEligible"] is True
    after_archived = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?archived=true&pageSize=50",
        headers=headers,
    )
    by_sam_arch = {i["samAccountName"]: i for i in after_archived.json()["items"]}
    assert by_sam_arch["orphan"]["qkaEligible"] is False

    # Picker returns only eligible + enabled + present.
    picker = client.get("/api/v1/admin/active-directory/eligible-users?search=employee", headers=headers)
    assert picker.status_code == 200
    picker_sams = {i["samAccountName"] for i in picker.json()["items"]}
    assert "employee1" in picker_sams
    assert "orphan" not in picker_sams

    # Disabled cannot enroll even if somehow eligible.
    with client.app.state.session_factory() as session:
        row = session.get(AdDirectoryUser, uuid.UUID(orphan["id"]))
        assert row is not None
        row.qka_eligible = True
        session.commit()
    enroll_disabled = client.post(
        "/api/v1/admin/active-directory/enroll",
        headers=headers,
        json={"objectGuid": orphan["objectGuid"]},
    )
    assert enroll_disabled.status_code == 400

    # AD-only user without email (department override required when AD dept blank).
    enroll_noemail = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={
            "identitySource": "active_directory",
            "objectGuid": noemail["objectGuid"],
            "displayName": "",
            "departmentOverride": "Operations",
            "localChatEnabled": False,
            "mcpEnabled": False,
        },
    )
    assert enroll_noemail.status_code == 200, enroll_noemail.text
    body = enroll_noemail.json()
    assert body["created"] is True
    assert body["primaryEmail"] == ""
    assert body["domainUsername"] == "noemail"
    assert body["department"] == "Operations"
    assert body["identitySource"] == "ad"
    assert body["localChatEnabled"] is False
    assert body["mcpEnabled"] is False
    assert body["adOnlyLoginImplemented"] is False
    qka_id = body["qkaUserId"]

    with client.app.state.session_factory() as session:
        chat = session.execute(
            select(ChatAccess).where(ChatAccess.qka_user_id == uuid.UUID(qka_id))
        ).scalar_one_or_none()
        assert chat is not None
        assert chat.local_chat_enabled is False
        assert chat.mcp_enabled is False
        user = session.get(QkaUser, uuid.UUID(qka_id))
        assert user is not None
        assert user.login_enabled is False
        assert user.contact_email is None

    # Duplicate GUID/SID rejected.
    dup = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={
            "identitySource": "active_directory",
            "objectGuid": noemail["objectGuid"],
            "displayName": "",
            "departmentOverride": "Operations",
        },
    )
    assert dup.status_code == 409

    # Directory list includes AD-only user alongside Google allowlist users.
    listing = client.get("/api/v1/access/users?search=noemail", headers=headers)
    assert listing.status_code == 200
    hit = next(i for i in listing.json()["items"] if i["qkaUserId"] == qka_id)
    assert hit["allowlistId"] is None
    assert hit["domainUsername"] == "noemail"

    # Seed a Google-linked QKA user matching employee1 mail — must not auto-merge.
    with client.app.state.session_factory() as session:
        now = datetime.now(timezone.utc)
        google_user = QkaUser(
            id=uuid.uuid4(),
            display_name="Employee One Google",
            status=0,
            login_enabled=True,
            is_directory_operator=False,
            is_super_admin=False,
            created_at=now,
            updated_at=now,
        )
        session.add(google_user)
        session.flush()
        gid = google_user.id
        g_ident = Ext(
            id=uuid.uuid4(),
            qka_user_id=gid,
            provider=PROVIDER_GOOGLE,
            stable_external_id="google-sub-employee1-link",
            primary_email="employee1@qehc.edu.sa",
            account_status=0,
            link_status=LINKED,
            directory_presence=0,
            created_at=now,
            updated_at=now,
        )
        session.add(g_ident)
        session.flush()
        session.add(
            GoogleWorkspaceAccount(
                id=uuid.uuid4(),
                external_identity_id=g_ident.id,
                google_user_id="google-sub-employee1-link",
                primary_email="employee1@qehc.edu.sa",
            )
        )
        session.commit()

    enroll_auto = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={
            "identitySource": "active_directory",
            "objectGuid": employee1["objectGuid"],
            "displayName": "",
            "contactEmail": "employee1@qehc.edu.sa",
        },
    )
    assert enroll_auto.status_code == 409
    assert "Link to Existing User" in enroll_auto.json()["detail"]

    enroll_link = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={
            "identitySource": "active_directory",
            "objectGuid": employee1["objectGuid"],
            "displayName": "",
            "contactEmail": "employee1@qehc.edu.sa",
            "linkExistingQkaUserId": str(gid),
        },
    )
    assert enroll_link.status_code == 200, enroll_link.text
    assert enroll_link.json()["linkedExisting"] is True
    assert enroll_link.json()["qkaUserId"] == str(gid)
    with client.app.state.session_factory() as session:
        google_still = session.execute(
            select(Ext).where(
                Ext.provider == PROVIDER_GOOGLE,
                Ext.stable_external_id == "google-sub-employee1-link",
                Ext.link_status == LINKED,
            )
        ).scalars().one()
        assert google_still.qka_user_id == gid
        ad_on_google = session.execute(
            select(Ext).where(
                Ext.provider == PROVIDER_AD,
                Ext.stable_external_id == "S-1-5-21-1-2-3-1001",
                Ext.link_status == LINKED,
            )
        ).scalars().one()
        assert ad_on_google.qka_user_id == gid

    # Unchecking eligibility must not revoke the enrolled QKA account.
    clear = client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/eligibility",
        headers=headers,
        json={"directoryUserIds": [noemail["id"]], "eligible": False},
    )
    assert clear.status_code == 200
    with client.app.state.session_factory() as session:
        still = session.get(QkaUser, uuid.UUID(qka_id))
        assert still is not None
        assert still.status == 0
        ad_ident = session.execute(
            select(Ext).where(
                Ext.provider == PROVIDER_AD,
                Ext.stable_external_id == "S-1-5-21-1-2-3-1003",
                Ext.link_status == LINKED,
            )
        ).scalars().first()
        assert ad_ident is not None


def test_unified_form_departments_and_ad_autofill(client):
    """Unified create: department catalog, AD autofill match, no Google allowlist from contact."""
    headers = _admin(client)
    depts = client.get("/api/v1/access/departments", headers=headers)
    assert depts.status_code == 200, depts.text
    values = {i["value"] for i in depts.json()["items"]}
    assert "IT" in values
    assert "Operations" in values

    _install_fake(client)
    cid = _sync_connection(client, headers)
    items = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?pageSize=50",
        headers=headers,
    ).json()["items"]
    employee1 = next(i for i in items if i["samAccountName"] == "employee1")
    client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/eligibility",
        headers=headers,
        json={"directoryUserIds": [employee1["id"]], "eligible": True},
    )
    # AD department IT matches catalog — no override required.
    created = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={
            "objectGuid": employee1["objectGuid"],
            "displayName": "Employee One QKA",
            "department": "IT",
            "contactEmail": "employee1@qehc.edu.sa",
            "localChatEnabled": True,
            "mcpEnabled": False,
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["department"] == "IT"
    assert body["displayName"] == "Employee One QKA"
    assert body["linkedIdentities"] == "ad"
    assert body["localChatEnabled"] is True
    assert body["allowlisted"] is False
    with client.app.state.session_factory() as session:
        assert (
            session.execute(
                select(AllowedEmail).where(AllowedEmail.email_normalized == "employee1@qehc.edu.sa")
            ).scalar_one_or_none()
            is None
        )

    # Duplicate AD enroll offers existing user (409 structured).
    dup = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={"objectGuid": employee1["objectGuid"], "department": "IT", "displayName": "Again"},
    )
    assert dup.status_code == 409
    detail = dup.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "ad_already_linked"
    assert detail["qkaUserId"] == body["qkaUserId"]

    # Edit AD user: department override + contact email; GUID/SID unchanged.
    edited = client.post(
        f"/api/v1/access/users/qka/{body['qkaUserId']}",
        headers=headers,
        json={
            "displayName": "Employee One Edited",
            "department": "Finance",
            "contactEmail": "employee1.alt@qehc.edu.sa",
            "localChatEnabled": False,
            "mcpEnabled": True,
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["displayName"] == "Employee One Edited"
    assert edited.json()["department"] == "Finance"
    assert edited.json()["contactEmail"] == "employee1.alt@qehc.edu.sa"
    assert edited.json()["mcpEnabled"] is True
    with client.app.state.session_factory() as session:
        ad = session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == PROVIDER_AD,
                ExternalIdentity.qka_user_id == uuid.UUID(body["qkaUserId"]),
                ExternalIdentity.link_status == LINKED,
            )
        ).scalars().one()
        assert ad.stable_external_id == "S-1-5-21-1-2-3-1001"


def test_ad_enroll_with_and_without_contact_email_single_row(client):
    """AD enrollment creates one canonical QkaUser; contact email must not create AllowedEmail."""
    Ext = ExternalIdentity
    _install_fake(client)
    headers = _admin(client)
    cid = _sync_connection(client, headers)
    items = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?pageSize=50",
        headers=headers,
    ).json()["items"]
    employee1 = next(i for i in items if i["samAccountName"] == "employee1")
    noemail = next(i for i in items if i["samAccountName"] == "noemail")

    mark = client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/eligibility",
        headers=headers,
        json={"directoryUserIds": [employee1["id"], noemail["id"]], "eligible": True},
    )
    assert mark.status_code == 200, mark.text

    # With optional contact email — contact only; no Google allowlist side-effect.
    with_email = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={
            "identitySource": "active_directory",
            "objectGuid": employee1["objectGuid"],
            "displayName": "",
            "department": "IT",
            "contactEmail": "employee1@qehc.edu.sa",
            "localChatEnabled": False,
            "mcpEnabled": False,
        },
    )
    assert with_email.status_code == 200, with_email.text
    body = with_email.json()
    assert body["created"] is True
    assert body["allowlisted"] is False
    assert body["allowlistId"] is None
    assert body["contactEmail"] == "employee1@qehc.edu.sa"
    assert body["identitySource"] == "ad"
    assert body["identityStatus"] == "ad_enrolled"
    assert body["accountStatus"] == "active"
    qka_with = body["qkaUserId"]

    with client.app.state.session_factory() as session:
        allow = session.execute(
            select(AllowedEmail).where(AllowedEmail.email_normalized == "employee1@qehc.edu.sa")
        ).scalar_one_or_none()
        assert allow is None
        user = session.get(QkaUser, uuid.UUID(qka_with))
        assert user is not None
        assert user.contact_email == "employee1@qehc.edu.sa"
        assert user.login_enabled is False
        ad_ident = session.execute(
            select(Ext).where(
                Ext.provider == PROVIDER_AD,
                Ext.qka_user_id == user.id,
                Ext.link_status == LINKED,
            )
        ).scalars().one()
        assert ad_ident.stable_external_id
        chat = session.execute(
            select(ChatAccess).where(ChatAccess.qka_user_id == user.id)
        ).scalar_one_or_none()
        assert chat is not None
        assert chat.local_chat_enabled is False
        assert chat.mcp_enabled is False

    listing = client.get("/api/v1/access/users?search=employee1", headers=headers)
    assert listing.status_code == 200
    hits = [i for i in listing.json()["items"] if i.get("qkaUserId") == qka_with or i.get("primaryEmail") == "employee1@qehc.edu.sa"]
    assert len(hits) == 1, hits
    assert hits[0]["identitySource"] == "ad"
    assert hits[0]["accountStatus"] == "active"
    assert hits[0]["identityStatus"] == "ad_enrolled"
    assert hits[0]["allowlistId"] is None

    # Without contact email — still one AD row.
    without = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={
            "identitySource": "active_directory",
            "objectGuid": noemail["objectGuid"],
            "displayName": "",
            "departmentOverride": "Operations",
        },
    )
    assert without.status_code == 200, without.text
    qka_no = without.json()["qkaUserId"]
    listing2 = client.get("/api/v1/access/users?search=noemail", headers=headers)
    hits2 = [i for i in listing2.json()["items"] if i.get("qkaUserId") == qka_no]
    assert len(hits2) == 1
    assert hits2[0]["allowlistId"] is None
    assert hits2[0]["identitySource"] == "ad"


def test_redundant_ad_contact_allowlist_cleanup(client):
    """Cleanup removes contact-only AllowedEmail; preserves AD QkaUser and refuses independent rows."""
    Ext = ExternalIdentity
    _install_fake(client)
    headers = _admin(client)
    cid = _sync_connection(client, headers)
    items = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?pageSize=50",
        headers=headers,
    ).json()["items"]
    employee1 = next(i for i in items if i["samAccountName"] == "employee1")
    client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/eligibility",
        headers=headers,
        json={"directoryUserIds": [employee1["id"]], "eligible": True},
    )
    # Enroll AD user with contact (no allowlist after fix).
    enrolled = client.post(
        "/api/v1/access/users",
        headers=headers,
        json={
            "identitySource": "active_directory",
            "objectGuid": employee1["objectGuid"],
            "displayName": "",
            "contactEmail": "employee1@qehc.edu.sa",
        },
    )
    assert enrolled.status_code == 200, enrolled.text
    qka_id = uuid.UUID(enrolled.json()["qkaUserId"])

    # Simulate legacy bug: insert redundant contact AllowedEmail + empty chat row.
    with client.app.state.session_factory() as session:
        now = datetime.now(timezone.utc)
        allow = AllowedEmail(
            id=uuid.uuid4(),
            email="employee1@qehc.edu.sa",
            email_normalized="employee1@qehc.edu.sa",
            enabled=True,
            display_name="Employee One",
            google_profile_name="",
            created_at=now,
            updated_at=now,
        )
        session.add(allow)
        session.flush()
        session.add(
            ChatAccess(
                id=uuid.uuid4(),
                allowed_email_id=allow.id,
                qka_user_id=None,
                local_chat_enabled=False,
                mcp_enabled=False,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        allow_id = allow.id

    # Duplicate display would have been two rows before list dedupe; cleanup removes the orphan.
    with client.app.state.session_factory() as session:
        result = directory.cleanup_redundant_ad_contact_allowlists(session, None, dry_run=False)
        session.commit()
        assert result["removedCount"] >= 1
        assert any(r["allowlistId"] == str(allow_id) for r in result["removed"])
        assert session.get(AllowedEmail, allow_id) is None
        assert session.get(QkaUser, qka_id) is not None
        assert session.execute(
            select(Ext).where(Ext.provider == PROVIDER_AD, Ext.qka_user_id == qka_id, Ext.link_status == LINKED)
        ).scalars().first() is not None

    listing = client.get("/api/v1/access/users?search=employee1", headers=headers)
    hits = [i for i in listing.json()["items"] if i.get("qkaUserId") == str(qka_id)]
    assert len(hits) == 1
    assert hits[0]["identitySource"] == "ad"


def test_eligibility_filters(client):
    _install_fake(client)
    headers = _admin(client)
    cid = _sync_connection(client, headers)
    users = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?accountStatus=disabled",
        headers=headers,
    )
    assert users.status_code == 200
    assert all(i["accountStatus"] == "disabled" for i in users.json()["items"])
    users2 = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?eligibility=eligible",
        headers=headers,
    )
    assert users2.json()["total"] == 0


def test_directory_users_sort(client):
    _install_fake(client)
    headers = _admin(client)
    cid = _sync_connection(client, headers)
    asc = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?sortBy=samAccountName&sortDesc=false&pageSize=50",
        headers=headers,
    )
    assert asc.status_code == 200, asc.text
    names_asc = [i["samAccountName"] for i in asc.json()["items"]]
    assert names_asc == sorted(names_asc, key=str.lower)
    assert asc.json()["sortBy"] == "samAccountName"
    assert asc.json()["sortDesc"] is False

    desc = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?sortBy=samAccountName&sortDesc=true&pageSize=50",
        headers=headers,
    )
    assert desc.status_code == 200, desc.text
    names_desc = [i["samAccountName"] for i in desc.json()["items"]]
    assert names_desc == sorted(names_desc, key=str.lower, reverse=True)
    assert names_desc != names_asc or len(names_asc) <= 1


def test_directory_users_archive_view(client):
    _install_fake(client)
    headers = _admin(client)
    cid = _sync_connection(client, headers)

    active = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?pageSize=50",
        headers=headers,
    )
    assert active.status_code == 200
    active_items = active.json()["items"]
    assert all(i["accountStatus"] == "enabled" for i in active_items)
    assert all(i["archived"] is False for i in active_items)
    assert any(i["samAccountName"] == "employee1" for i in active_items)
    assert all(i["samAccountName"] != "orphan" for i in active_items)

    archived = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?archived=true&pageSize=50",
        headers=headers,
    )
    assert archived.status_code == 200
    arch_items = archived.json()["items"]
    assert any(i["samAccountName"] == "orphan" for i in arch_items)
    assert all(i["archived"] is True for i in arch_items)

    employee1 = next(i for i in active_items if i["samAccountName"] == "employee1")
    # Mark eligible then refuse archive.
    mark = client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/eligibility",
        headers=headers,
        json={"directoryUserIds": [employee1["id"]], "eligible": True},
    )
    assert mark.status_code == 200
    refused = client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/archive",
        headers=headers,
        json={"directoryUserIds": [employee1["id"]], "archived": True},
    )
    assert refused.status_code == 200
    assert refused.json()["updated"] == 0
    assert refused.json()["skipped"]

    # Clear eligibility, then archive.
    client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/eligibility",
        headers=headers,
        json={"directoryUserIds": [employee1["id"]], "eligible": False},
    )
    archived_ok = client.post(
        f"/api/v1/admin/active-directory/connections/{cid}/archive",
        headers=headers,
        json={"directoryUserIds": [employee1["id"]], "archived": True},
    )
    assert archived_ok.status_code == 200, archived_ok.text
    assert archived_ok.json()["updated"] == 1
    active2 = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?pageSize=50",
        headers=headers,
    )
    assert all(i["samAccountName"] != "employee1" for i in active2.json()["items"])


def test_directory_users_partial_search(client):
    _install_fake(client)
    headers = _admin(client)
    cid = _sync_connection(client, headers)

    # Name/username substring — must not match solely on "@qehc.edu.sa".
    by_name = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?search=ploy&pageSize=50",
        headers=headers,
    )
    assert by_name.status_code == 200, by_name.text
    items = by_name.json()["items"]
    assert items
    assert all(
        "ploy" in (i["displayName"] + i["samAccountName"]).lower()
        or "ploy" in (i.get("department") or "").lower()
        for i in items
    )

    # Domain fragment alone must not match every "@qehc.edu.sa" UPN/mail.
    all_users = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?pageSize=50",
        headers=headers,
    )
    domain_hit = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?search=edu&pageSize=50",
        headers=headers,
    )
    assert domain_hit.status_code == 200
    assert domain_hit.json()["total"] < all_users.json()["total"]
    assert all(
        "edu" in (
            (i["displayName"] or "")
            + (i["samAccountName"] or "")
            + (i.get("department") or "")
            + ((i.get("userPrincipalName") or "").split("@")[0])
            + ((i.get("mail") or "").split("@")[0])
        ).lower()
        for i in domain_hit.json()["items"]
    )

    # Department partial: active fixture uses IT (Finance is on disabled orphan → archived list).
    by_dept = client.get(
        f"/api/v1/admin/active-directory/connections/{cid}/users?department=it&pageSize=50",
        headers=headers,
    )
    assert by_dept.status_code == 200, by_dept.text
    assert by_dept.json()["total"] >= 1
    assert all("it" in (i.get("department") or "").lower() for i in by_dept.json()["items"])
