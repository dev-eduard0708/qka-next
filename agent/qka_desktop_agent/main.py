"""QKA Desktop Agent entrypoint (silent background loop)."""

from __future__ import annotations

import logging
import os
import socket
import sys
import time
from pathlib import Path

from qka_desktop_agent import __version__
from qka_desktop_agent.client import AgentApiError, AgentClient
from qka_desktop_agent.config import device_fingerprint, load_config, save_credentials
from qka_desktop_agent.index_store import LocalIndex
from qka_desktop_agent.readonly import run_disposable_fixture_negative_write_test
from qka_desktop_agent.scanner import scan_once

log = logging.getLogger("qka.agent")


def _configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


def _domain_name() -> str:
    return os.environ.get("USERDOMAIN") or socket.getfqdn()


def ensure_enrolled(client: AgentClient, cfg) -> None:
    if cfg.device_id and cfg.device_key:
        client.set_credentials(cfg.device_id, cfg.device_key)
        return
    if not cfg.enrollment_code:
        raise RuntimeError(
            "No device credentials and no enrollment code. "
            "Set QKA_AGENT_ENROLLMENT_CODE or agent.config.json after admin issues a code."
        )
    if not cfg.server_base_url:
        raise RuntimeError("QKA_AGENT_SERVER_URL / serverBaseUrl is required.")
    if cfg.server_base_url.lower().startswith("http://127.0.0.1") or "localhost" in cfg.server_base_url.lower():
        log.warning("server_base_url looks like loopback; employee PCs must use reachable internal HTTPS.")
    result = client.enroll(
        {
            "enrollmentCode": cfg.enrollment_code,
            "computerName": os.environ.get("COMPUTERNAME") or socket.gethostname(),
            "deviceFingerprint": device_fingerprint(),
            "domainJoinedName": _domain_name(),
            "agentVersion": __version__,
        }
    )
    save_credentials(cfg, result["deviceId"], result["deviceKey"])
    client.set_credentials(cfg.device_id, cfg.device_key)
    log.info("enrolled device_id=%s", cfg.device_id)


def run_loop(stop_flag: list[bool] | None = None) -> int:
    cfg = load_config()
    _configure_logging(cfg.log_path)
    log.info("qka_desktop_agent starting version=%s", __version__)
    fixture = run_disposable_fixture_negative_write_test(cfg.data_dir)
    log.info("readonly_fixture %s", fixture.detail)

    if not cfg.server_base_url:
        log.error("serverBaseUrl missing")
        return 2

    client = AgentClient(cfg.server_base_url, verify_tls=cfg.verify_tls)
    try:
        ensure_enrolled(client, cfg)
    except Exception as exc:  # noqa: BLE001
        log.exception("enrollment_failed")
        print(f"enrollment_failed: {exc}", file=sys.stderr)
        return 3

    index = LocalIndex(cfg.index_path)
    paused = False

    def should_pause() -> bool:
        return paused or bool(stop_flag and stop_flag[0])

    while True:
        if stop_flag and stop_flag[0]:
            break
        try:
            client.heartbeat(
                {
                    "agentVersion": __version__,
                    "computerName": os.environ.get("COMPUTERNAME") or socket.gethostname(),
                    "health": "ok",
                }
            )
            policy = client.policy()
            command = (policy.get("command") or "run").lower()
            if command == "pause":
                paused = True
            elif command in {"run", "start", "resume"}:
                paused = False
            elif command == "stop":
                log.warning("device revoked/stopped by server")
                break

            folders = policy.get("approvedFolders") or []
            if paused:
                counts = index.counts()
                client.progress(
                    {
                        "indexingState": "paused",
                        "readOnlyVerified": False,
                        "readOnlyDetail": "Paused by policy.",
                        **{k: counts[k] for k in ("filesTotal", "filesIndexed", "filesPending", "filesFailed", "filesByType")},
                    }
                )
            elif folders:
                def on_progress(payload: dict) -> None:
                    client.progress(
                        {
                            "indexingState": payload.get("indexingState", "running"),
                            "readOnlyVerified": bool(payload.get("readOnlyVerified")),
                            "readOnlyDetail": payload.get("readOnlyDetail") or "",
                            "filesTotal": payload.get("filesTotal", 0),
                            "filesIndexed": payload.get("filesIndexed", 0),
                            "filesPending": payload.get("filesPending", 0),
                            "filesFailed": payload.get("filesFailed", 0),
                            "filesByType": payload.get("filesByType") or {},
                            "lastError": payload.get("lastError") or "",
                        }
                    )

                result = scan_once(index, folders, should_pause=should_pause, on_progress=on_progress)
                on_progress(result)
            else:
                counts = index.counts()
                client.progress(
                    {
                        "indexingState": "idle",
                        "readOnlyVerified": True,
                        "readOnlyDetail": "No approved folders configured yet.",
                        **{k: counts[k] for k in ("filesTotal", "filesIndexed", "filesPending", "filesFailed", "filesByType")},
                    }
                )
        except AgentApiError as exc:
            log.error("api_error %s", exc)
            try:
                client.report_error(str(exc), category="api")
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            log.exception("loop_error")
            try:
                client.report_error(str(exc), category="agent")
            except Exception:  # noqa: BLE001
                pass
        time.sleep(max(10, int(cfg.poll_seconds)))
    index.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "service":
        from qka_desktop_agent.win_service import run_service

        return run_service()
    return run_loop()


if __name__ == "__main__":
    raise SystemExit(main())
