from tests.conftest import ALLOWED_USER, DENIED_USER, FILE_ID, GOOGLE_ONLY_USER, SHARE_ID, auth_cookies
from tests.test_source_access import _admin_headers


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

    quoted = client.get("/api/v1/search", params={"q": '"Laptop"'}, cookies=cookies)
    assert quoted.status_code == 200, quoted.text
    assert quoted.json()["error"] is None
    assert len(quoted.json()["results"]) == 1
    assert quoted.json()["results"][0]["id"] == FILE_ID.hex
    assert quoted.json()["results"][0]["sizeBytes"] >= 0


def test_search_catalog_requires_auth(client) -> None:
    response = client.get("/api/v1/search/catalog")
    assert response.status_code == 401


def test_search_catalog_shows_authorized_source_and_excel_count(client) -> None:
    cookies = auth_cookies(ALLOWED_USER)
    catalog = client.get("/api/v1/search/catalog", cookies=cookies)
    assert catalog.status_code == 200, catalog.text
    body = catalog.json()
    assert body["error"] is None
    assert body["totalFiles"] >= 1
    eduard = next(row for row in body["sources"] if row["shareName"] == "eduard")
    assert eduard["totalFiles"] >= 1
    types = {item["key"]: item["count"] for item in eduard["types"]}
    assert types.get("excel", 0) >= 1
    assert all(item["count"] > 0 for item in eduard["types"])
    assert any(item["icon"] == "table_chart" for item in eduard["types"] if item["key"] == "excel")


def test_search_catalog_hides_unauthorized_sources(client) -> None:
    cookies = auth_cookies(DENIED_USER)
    catalog = client.get("/api/v1/search/catalog", cookies=cookies)
    assert catalog.status_code == 200
    assert catalog.json()["sources"] == []
    assert catalog.json()["totalFiles"] == 0


def test_search_catalog_qka_managed_grant_counts_excel(client) -> None:
    empty = client.get("/api/v1/search/catalog", cookies=auth_cookies(GOOGLE_ONLY_USER))
    assert empty.json()["sources"] == []
    headers = _admin_headers(client)
    reg = client.post(
        "/api/v1/access/source/scopes/corporate",
        headers=headers,
        json={
            "approvedShareId": str(SHARE_ID),
            "folderPrefix": "EDUARDTESTCLOUD",
            "displayName": "catalog",
            "sourceOwnerName": "Owner",
            "sourceOwnerEmail": "owner@qehc.edu.sa",
            "approvalReference": "CATALOG-1",
            "serviceReadVerified": True,
        },
    )
    assert reg.status_code == 200, reg.text
    grant = client.post(
        "/api/v1/access/source/grants",
        headers=headers,
        json={
            "scopeId": reg.json()["id"],
            "qkaUserId": str(GOOGLE_ONLY_USER),
            "capabilityView": True,
            "pathMode": "all",
            "fileTypes": ["excel"],
            "reason": "catalog",
        },
    )
    assert grant.status_code == 200, grant.text
    client.cookies.clear()
    catalog = client.get("/api/v1/search/catalog", cookies=auth_cookies(GOOGLE_ONLY_USER))
    assert catalog.status_code == 200, catalog.text
    body = catalog.json()
    assert body["totalFiles"] >= 1
    types = {item["key"]: item["count"] for src in body["sources"] for item in src["types"]}
    assert types.get("excel", 0) >= 1
    assert "pdf" not in types


def test_other_user_is_denied_document(client) -> None:
    cookies = auth_cookies(DENIED_USER)
    search = client.get("/api/v1/search", params={"q": "Laptop"}, cookies=cookies)
    assert search.status_code == 200
    assert search.json()["results"] == []
    fetch = client.get(f"/api/v1/documents/{FILE_ID.hex}", cookies=cookies)
    assert fetch.json()["denied"] is True
