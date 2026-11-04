"""DNS + ICMP probes for authorized AD workstations only.

Never used for unrestricted network scans. A missing ICMP reply must not be
interpreted as the computer being powered off.
"""

from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass
class DnsResult:
    ok: bool
    ip: str
    detail: str


@dataclass
class PingResult:
    replied: bool
    detail: str


class NetworkProbe(Protocol):
    def resolve_dns(self, hostname: str) -> DnsResult: ...

    def icmp_ping(self, host: str, *, timeout_ms: int = 2000) -> PingResult: ...


class RealNetworkProbe:
    def resolve_dns(self, hostname: str) -> DnsResult:
        host = (hostname or "").strip().rstrip(".")
        if not host:
            return DnsResult(False, "", "No DNS hostname available.")
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            return DnsResult(False, "", f"DNS resolution failed: {exc}")
        except OSError as exc:
            return DnsResult(False, "", f"DNS resolution error: {exc}")
        for info in infos:
            addr = info[4][0] if info[4] else ""
            if addr:
                return DnsResult(True, addr, f"Resolved {host} → {addr}")
        return DnsResult(False, "", f"DNS returned no addresses for {host}")

    def icmp_ping(self, host: str, *, timeout_ms: int = 2000) -> PingResult:
        target = (host or "").strip()
        if not target:
            return PingResult(False, "No host to ping.")
        timeout_ms = max(200, min(int(timeout_ms), 10000))
        if os.name == "nt":
            # -n 1 one echo; -w timeout in ms
            cmd = ["ping", "-n", "1", "-w", str(timeout_ms), target]
        else:
            # -c 1 one echo; -W timeout in seconds (ceil)
            secs = max(1, (timeout_ms + 999) // 1000)
            cmd = ["ping", "-c", "1", "-W", str(secs), target]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=(timeout_ms / 1000.0) + 3.0,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return PingResult(
                False,
                "ICMP probe timed out. No reply does not mean the computer is powered off "
                "(firewall or ICMP may be blocked).",
            )
        except OSError as exc:
            return PingResult(False, f"ICMP probe could not run: {exc}")
        if completed.returncode == 0:
            return PingResult(True, "ICMP echo reply received.")
        return PingResult(
            False,
            "No ICMP reply. This may mean the host is offline, firewalled, or ICMP is blocked — "
            "not conclusive powered-off evidence.",
        )


_default_probe: NetworkProbe | None = None


def default_network_probe() -> NetworkProbe:
    global _default_probe
    if _default_probe is None:
        _default_probe = RealNetworkProbe()
    return _default_probe
