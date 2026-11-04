"""Windows ADSI requires COM init on FastAPI worker threads."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from qka_api.ldap_client import (
    AUTH_WINDOWS_INTEGRATED,
    LdapConnectParams,
    RealLdapGateway,
    _safe_kerberos_error,
    ensure_windows_com,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows COM/ADSI only")
def test_ensure_windows_com_on_worker_thread():
    def run():
        return ensure_windows_com()

    with ThreadPoolExecutor(max_workers=1) as pool:
        mode = pool.submit(run).result()
    assert mode in {"mta", "sta", "already"}


@pytest.mark.skipif(os.name != "nt", reason="Windows COM/ADSI only")
def test_real_gateway_diagnose_succeeds_on_worker_thread():
    """Regression: UI Test Connection uses FastAPI threadpool (not main thread)."""

    def run():
        gw = RealLdapGateway()
        params = LdapConnectParams(
            domain_dns_name="qehc.edu.sa",
            domain_controller="DC1.qehc.edu.sa",
            port=389,
            auth_mode=AUTH_WINDOWS_INTEGRATED,
            verify_tls=False,
        )
        return gw.diagnose(params)

    with ThreadPoolExecutor(max_workers=1) as pool:
        ok, err, checks, base = pool.submit(run).result()
    assert ok is True, err
    assert base == "DC=qehc,DC=edu,DC=sa"
    by_name = {c.name: c for c in checks}
    assert by_name["kerberos"].status == "pass"
    assert by_name["signing"].status == "pass"
    assert by_name["sealing"].status == "pass"
    assert by_name["rootdse"].status == "pass"
    assert by_name["search"].status == "pass"


def test_safe_kerberos_error_includes_com_hresult():
    class FakeComError(Exception):
        hresult = -2147221020  # 0x800401E4

    msg = _safe_kerberos_error(FakeComError("(-2147221020, 'Invalid syntax', None, None)"))
    assert "0x800401E4" in msg
    assert "COM/ADSI" in msg
