from tests.conftest import ALLOWED_USER, DENIED_USER, FILE_ID, auth_cookies


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_search_requires_auth(client) -> None:
    response = client.get("/api/v1/search", params={"q": "Laptop"})
    assert response.status_code == 401


def test_search_rejects_spoofed_user_id(client) -> None:
    response = client.get(
        "/api/v1/search",
        params={"q": "Laptop", "userId": str(ALLOWED_USER)},
        cookies=auth_cookies(DENIED_USER),
    )
    assert response.status_code == 400


def test_authorized_user_can_search_and_fetch(client) -> None:
    cookies = auth_cookies(ALLOWED_USER)
    search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search.status_code == 200, search.text
    body = search.json()
    assert body["error"] is None
    assert len(body["results"]) == 1
    assert body["results"][0]["id"] == FILE_ID.hex

    fetch = client.get(f"/api/v1/documents/{FILE_ID.hex}", cookies=cookies)
    assert fetch.status_code == 200
    detail = fetch.json()
    assert detail["denied"] is False
    assert "Laptop" in detail["text"]


def test_other_user_is_denied_document(client) -> None:
    cookies = auth_cookies(DENIED_USER)
    search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search.status_code == 200
    assert search.json()["results"] == []
    fetch = client.get(f"/api/v1/documents/{FILE_ID.hex}", cookies=cookies)
    assert fetch.json()["denied"] is True
