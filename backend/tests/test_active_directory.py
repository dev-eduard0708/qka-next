from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from qka_api.services import active_directory as ad
from qka_shared.allowlist import LINKED, PROVIDER_AD, PROVIDER_GOOGLE
from qka_shared.models import ExternalIdentity, GoogleWorkspaceAccount, QkaUser
from tests.fake_ldap import FakeLdapGateway


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
        json={
            "connectionName": "AD",
            "domainDnsName": "ad.example.local",
            "domainController": "dc01.ad.example.local",
            "bindUsername": "DOMAIN\\reader",
            "bindPassword": "secret",
            "verifyTls": True,
        },
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
        json={
            "domainDnsName": "ad.example.local",
            "domainController": "dc01.ad.example.local",
            "bindUsername": "DOMAIN\\reader",
            "bindPassword": "bad",
        },
    )
    assert bind_fail.json()["succeeded"] is False
    assert any(c["name"] == "bind" and c["status"] == "failed" for c in bind_fail.json()["checks"])


def test_save_requires_successful_test_and_hides_secret(client):
    gateway = _install_fake(client)
    headers = _admin(client)
    gateway.fail_bind = True
    denied = client.post(
        "/api/v1/admin/active-directory/connections",
        headers=headers,
        json={
            "connectionName": "Corp AD",
            "domainDnsName": "ad.example.local",
            "domainController": "dc01.ad.example.local",
            "bindUsername": "DOMAIN\\reader",
            "bindPassword": "secret-value",
        },
    )
    assert denied.status_code == 400

    gateway.fail_bind = False
    ad._TEST_RATE.clear()
    created = client.post(
        "/api/v1/admin/active-directory/connections",
        headers=headers,
        json={
            "connectionName": "Corp AD",
            "domainDnsName": "ad.example.local",
            "domainController": "dc01.ad.example.local",
            "bindUsername": "DOMAIN\\reader",
            "bindPassword": "secret-value",
        },
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["bindSecretConfigured"] is True
    assert "bindPassword" not in payload
    assert "secret-value" not in created.text
    assert payload["protocol"] == "ldaps"
    assert payload["connectionStatus"] == ad.STATUS_CONNECTED


def test_ou_user_group_sync_and_identity_link(client):
    gateway = _install_fake(client)
    headers = _admin(client)
    created = client.post(
        "/api/v1/admin/active-directory/connections",
        headers=headers,
        json={
            "connectionName": "Corp AD",
            "domainDnsName": "ad.example.local",
            "domainController": "dc01.ad.example.local",
            "bindUsername": "DOMAIN\\reader",
            "bindPassword": "secret-value",
        },
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
        # Preserve existing Google rows for other users — just adding.
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
