from __future__ import annotations

import httpx

base = "http://127.0.0.1:5265"
c = httpx.Client(base_url=base, follow_redirects=True)
health = c.get("/health")
print("HEALTH", health.status_code, health.json())
status = c.get("/api/v1/auth/status")
print(
    "AUTH_STATUS",
    status.status_code,
    {k: status.json().get(k) for k in ("googleConfigured", "googleHostedDomain", "devSessionEnabled")},
)
login = c.post("/api/v1/auth/login", json={"username": "super", "password": "123"})
print(
    "LOGIN",
    login.status_code,
    {"superAdmin": login.json().get("superAdmin"), "displayName": login.json().get("displayName")},
)
csrf = c.cookies.get("qka_next_csrf")
headers = {"X-CSRF-Token": csrf} if csrf else {}
conn = c.get("/api/v1/admin/synology/connection")
body = conn.json()
blob = conn.text.lower()
print(
    "CONNECTION",
    conn.status_code,
    {
        "host": body.get("hostName"),
        "path": body.get("configuredSharedFolderPath"),
        "authMode": body.get("authMode"),
        "secretConfigured": body.get("serviceAccountSecretConfigured"),
        "status": body.get("connectionStatus"),
        "hasPasswordKey": "password" in body,
        "secretLeaked": "serviceaccountsecretprotected" in blob,
    },
)
mon = c.get("/api/v1/admin/synology/sync/monitor")
monitor = mon.json()
stats = monitor.get("stats") or {}
print(
    "MONITOR",
    mon.status_code,
    {
        "totalFiles": stats.get("totalFiles"),
        "totalBytes": stats.get("totalBytes"),
        "excel": stats.get("excel"),
        "pdf": stats.get("pdf"),
        "word": stats.get("word"),
        "ppt": stats.get("powerPoint"),
        "csv": stats.get("csv"),
        "other": stats.get("other"),
        "lastSync": monitor.get("lastSuccessfulSyncAt"),
        "job": (monitor.get("currentJob") or {}).get("status"),
    },
)
inv = c.get("/api/v1/admin/synology/inventory", params={"page": 1, "pageSize": 5})
page = inv.json()
print(
    "INVENTORY",
    inv.status_code,
    {
        "total": page.get("totalCount"),
        "pageItems": len(page.get("items") or []),
        "sampleKeys": list((page.get("items") or [{}])[0].keys()),
    },
)
tested = c.post("/api/v1/admin/synology/connection/test", headers=headers)
payload = tested.json()
print(
    "SMB_TEST_API",
    tested.status_code,
    {
        "succeeded": payload.get("succeeded"),
        "port445": payload.get("port445Reachable"),
        "authShare": payload.get("authenticatedShareAccess"),
        "safeError": payload.get("safeError") or payload.get("detail"),
    },
)
start = c.post("/api/v1/admin/synology/sync/start", headers=headers)
print("SYNC_START", start.status_code, start.json())
denied = httpx.Client(base_url=base)
print("UNAUTH_CONN", denied.get("/api/v1/admin/synology/connection").status_code)
