from tests.conftest import ALLOWED_USER, auth_cookies


def _csrf(client) -> dict[str, str]:
    token = client.cookies.get("qka_next_csrf")
    return {"X-CSRF-Token": token} if token else {}


def test_password_login_rejects_wrong_password(client) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "super", "password": "wrong"})
    assert response.status_code == 401
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 401


def test_super_admin_password_login_and_allowlist(client) -> None:
    login = client.post("/api/v1/auth/login", json={"username": "super", "password": "123"})
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["superAdmin"] is True
    assert body["displayName"]
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["superAdmin"] is True

    listed = client.get("/api/v1/access/emails")
    assert listed.status_code == 200
    before = {row["email"] for row in listed.json()}

    created = client.post(
        "/api/v1/access/emails",
        json={"email": "new.person@qehc.edu.sa"},
        headers=_csrf(client),
    )
    assert created.status_code == 200, created.text
    assert created.json()["email"] == "new.person@qehc.edu.sa"
    assert created.json()["enabled"] is True
    after = client.get("/api/v1/access/emails")
    emails = {row["email"] for row in after.json()}
    assert "new.person@qehc.edu.sa" in emails
    assert before.issubset(emails)

    disabled = client.post(
        f"/api/v1/access/emails/{created.json()['id']}/enabled",
        json={"enabled": False},
        headers=_csrf(client),
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    removed = client.post(
        f"/api/v1/access/emails/{created.json()['id']}/remove",
        headers=_csrf(client),
    )
    assert removed.status_code == 200


def test_non_admin_cannot_manage_allowlist(client) -> None:
    listed = client.get("/api/v1/access/emails", cookies=auth_cookies(ALLOWED_USER))
    assert listed.status_code == 403
    added = client.post(
        "/api/v1/access/emails",
        json={"email": "intruder@qehc.edu.sa"},
        cookies=auth_cookies(ALLOWED_USER),
        headers={"X-CSRF-Token": "missing"},
    )
    assert added.status_code in {403, 401}


def test_allowlist_rejects_other_domains(client) -> None:
    login = client.post("/api/v1/auth/login", json={"username": "super", "password": "123"})
    assert login.status_code == 200
    response = client.post(
        "/api/v1/access/emails",
        json={"email": "someone@gmail.com"},
        headers=_csrf(client),
    )
    assert response.status_code == 400
