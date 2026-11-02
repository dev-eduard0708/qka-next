"""Post-cutover MCP verification against QKA-Next :5265 (inspector bearer)."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:5265"
BEARER = "qka-mcp-pilot-dev-token-change-me"
MCP_URLS = (f"{BASE}/mcp", f"{BASE}/mcp/")


def post_mcp(method: str, params: dict, req_id: int = 1) -> tuple[int, str]:
    body = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}).encode()
    last = (0, "")
    for url in MCP_URLS:
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {BEARER}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            last = (exc.code, raw)
            if exc.code not in {307, 308}:
                return exc.code, raw
    return last


def get(path: str, cookie: str | None = None) -> tuple[int, dict | str]:
    headers = {}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(f"{BASE}{path}", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def main() -> int:
    print("=== initialize ===")
    code, text = post_mcp(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "cutover-test", "version": "1"},
        },
        1,
    )
    print(code, text[:400])

    print("=== tools/list ===")
    code, text = post_mcp("tools/list", {}, 2)
    print(code, text[:600])

    print("=== get_profile ===")
    code, text = post_mcp("tools/call", {"name": "get_profile", "arguments": {}}, 3)
    print(code, text[:600])

    print("=== search Laptop ===")
    code, text = post_mcp("tools/call", {"name": "search", "arguments": {"query": "Laptop"}}, 4)
    print(code, text[:800])

    print("=== fetch deny ===")
    code, text = post_mcp(
        "tools/call",
        {"name": "fetch", "arguments": {"id": "00000000000000000000000000000000"}},
        5,
    )
    print(code, text[:600])

    # login super for dashboard
    login_body = json.dumps({"username": "super", "password": "123"}).encode()
    login_req = urllib.request.Request(
        f"{BASE}/api/v1/auth/login",
        data=login_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(login_req, timeout=20) as resp:
        cookie = resp.headers.get("Set-Cookie", "")
        # collect session cookie simply
        cookies = []
        for h in resp.headers.get_all("Set-Cookie") or []:
            cookies.append(h.split(";", 1)[0])
        cookie_header = "; ".join(cookies) if cookies else cookie.split(";", 1)[0]

    print("=== dashboard status ===")
    code, payload = get("/api/v1/admin/integrations/chatgpt/status", cookie=cookie_header)
    if isinstance(payload, dict):
        tunnel = payload.get("secureMcpTunnel", {})
        mcp = payload.get("mcpServer", {})
        chatgpt = payload.get("chatgptBusiness", {})
        tools = payload.get("mcpTools", {})
        print(
            json.dumps(
                {
                    "tunnel": tunnel.get("status"),
                    "tunnelError": tunnel.get("error"),
                    "mcp": mcp.get("status"),
                    "authReady": mcp.get("authReady"),
                    "endpoint": mcp.get("endpoint"),
                    "chatgpt": chatgpt.get("status"),
                    "plugin": chatgpt.get("pluginName"),
                    "toolSuccess": tools.get("successCount"),
                    "toolFail": tools.get("failureCount"),
                },
                indent=2,
            )
        )
    else:
        print(code, payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
