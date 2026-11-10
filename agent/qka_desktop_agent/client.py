"""HTTPS client for QKA Desktop Agent APIs."""

from __future__ import annotations

import json
import ssl
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AgentApiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _ssl_context(*, verify_tls: bool) -> ssl.SSLContext | None:
    """Build a TLS context that trusts the Windows cert stores on OpenSSL builds.

    PyInstaller bundles libssl, which often ignores LocalMachine\\Root unless we
    explicitly load Windows CA/ROOT certificates into the OpenSSL context.
    """
    if not verify_tls:
        return ssl._create_unverified_context()  # noqa: S323 — explicit pilot override only
    ctx = ssl.create_default_context()
    if sys.platform != "win32":
        return ctx
    try:
        pem_parts: list[str] = []
        for store_name in ("CA", "ROOT"):
            for der_bytes, encoding, _trust in ssl.enum_certificates(store_name):
                if encoding == "x509_asn" and der_bytes:
                    pem_parts.append(ssl.DER_cert_to_PEM_cert(der_bytes))
        if pem_parts:
            ctx.load_verify_locations(cadata="".join(pem_parts))
    except Exception:
        # Fall back to create_default_context() paths / SSL_CERT_FILE.
        pass
    return ctx


class AgentClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0, verify_tls: bool = True):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify_tls = verify_tls
        self._ssl_context = _ssl_context(verify_tls=verify_tls)
        self.device_id: str | None = None
        self.device_key: str | None = None

    def set_credentials(self, device_id: str, device_key: str) -> None:
        self.device_id = device_id
        self.device_key = device_key

    def enroll(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/agent/desktop/enroll", payload, auth=False)

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/agent/desktop/heartbeat", payload)

    def policy(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/agent/desktop/policy")

    def progress(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/agent/desktop/progress", payload)

    def report_error(self, message: str, category: str = "agent") -> dict[str, Any]:
        return self._request("POST", "/api/v1/agent/desktop/errors", {"message": message, "category": category})

    def folder_browse_result(self, browse_id: str, *, nodes: list[dict], error: str = "") -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/agent/desktop/folder-browse/{browse_id}/result",
            {"nodes": nodes, "error": error},
        )

    def inventory(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/agent/desktop/inventory", payload)

    def _request(self, method: str, path: str, body: dict | None = None, *, auth: bool = True) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "QKA-Desktop-Agent/0.1"}
        if auth:
            if not self.device_id or not self.device_key:
                raise AgentApiError("Device credentials are not configured.")
            headers["X-QKA-Device-Id"] = self.device_id
            headers["X-QKA-Device-Key"] = self.device_key
            headers["Authorization"] = f"Bearer {self.device_id}.{self.device_key}"
        req = Request(url, data=data, headers=headers, method=method)
        context = self._ssl_context if url.lower().startswith("https:") else None
        try:
            with urlopen(req, timeout=self.timeout, context=context) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise AgentApiError(f"HTTP {exc.code}: {detail}", status=exc.code) from exc
        except URLError as exc:
            raise AgentApiError(f"Network error: {exc.reason}") from exc
