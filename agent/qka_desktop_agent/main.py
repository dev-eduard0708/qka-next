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
from qka_desktop_agent.config import (
    device_fingerprint,
    load_config,
    resolve_machine_enrollment_token,
    save_credentials,
)
from qka_desktop_agent.index_store import LocalIndex
from qka_desktop_agent.folder_browser import browse_children, nodes_to_json, resolve_known_folders
from qka_desktop_agent.readonly import run_disposable_fixture_negative_write_test
from qka_desktop_agent.scanner import scan_once
from qka_desktop_agent.win_identity import local_computer_sid, machine_enrollment_proof

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
    if not cfg.server_base_url:
        raise RuntimeError("QKA_AGENT_SERVER_URL / serverBaseUrl is required.")
    if cfg.server_base_url.lower().startswith("http://127.0.0.1") or "localhost" in cfg.server_base_url.lower():
        log.warning("server_base_url looks like loopback; employee PCs must use reachable internal HTTPS.")

    # Reload config each attempt so a token staged after MSI install is picked up.
    fresh = load_config()
    cfg.machine_enrollment_token = fresh.machine_enrollment_token
    cfg.enrollment_code = fresh.enrollment_code
    cfg.token_drop_unc = fresh.token_drop_unc
    if not cfg.server_base_url:
        cfg.server_base_url = fresh.server_base_url

    identity = local_computer_sid()
    machine_token = resolve_machine_enrollment_token(cfg, identity.computer_sid or "")
    if not cfg.enrollment_code and not machine_token:
        raise RuntimeError(
            "No device credentials and no enrollment token yet. "
            "Waiting for per-computer token-drop (Network Service / computer$) or enrollment code."
        )

    payload = {
        "computerName": os.environ.get("COMPUTERNAME") or socket.gethostname(),
        "deviceFingerprint": device_fingerprint(),
        "domainJoinedName": _domain_name(),
        "agentVersion": __version__,
    }
    if machine_token:
        if not identity.computer_sid:
            raise RuntimeError("Could not resolve local computer SID for machine-identity enrollment.")
        payload.update(
            {
                "machineEnrollmentToken": machine_token,
                "adComputerSid": identity.computer_sid,
                "sidSource": "local_machine",
                "machineProof": machine_enrollment_proof(
                    token=machine_token,
                    computer_sid=identity.computer_sid,
                    computer_guid="",
                ),
            }
        )
    else:
        payload["enrollmentCode"] = cfg.enrollment_code
    result = client.enroll(payload)
    save_credentials(cfg, result["deviceId"], result["deviceKey"])
    client.set_credentials(cfg.device_id, cfg.device_key)
    log.info("enrolled device_id=%s", cfg.device_id)


def run_loop(stop_flag: list[bool] | None = None) -> int:
    cfg = load_config()
    _configure_logging(cfg.log_path)
    log.info(
        "qka_desktop_agent starting version=%s identity_hint=NetworkService_computer_account",
        __version__,
    )
    fixture = run_disposable_fixture_negative_write_test(cfg.data_dir)
    log.info("readonly_fixture %s", fixture.detail)

    if not cfg.server_base_url:
        log.error("serverBaseUrl missing")
        return 2

    client = AgentClient(cfg.server_base_url, verify_tls=cfg.verify_tls)
    enrolled = bool(cfg.device_id and cfg.device_key)
    index: LocalIndex | None = None
    paused = False
    enrollment_failures = 0

    def should_pause() -> bool:
        return paused or bool(stop_flag and stop_flag[0])

    while True:
        if stop_flag and stop_flag[0]:
            break

        if not enrolled:
            try:
                ensure_enrolled(client, cfg)
                enrolled = True
                enrollment_failures = 0
                index = LocalIndex(cfg.index_path)
                log.info("enrollment_ready — entering scan loop")
            except Exception as exc:  # noqa: BLE001
                enrollment_failures += 1
                # Do not exit: token may be staged after MSI/GPO first boot.
                log.warning(
                    "enrollment_pending attempt=%s detail=%s",
                    enrollment_failures,
                    str(exc)[:300],
                )
                time.sleep(max(15, min(120, 15 + enrollment_failures * 5)))
                continue

        assert index is not None
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

            for browse in policy.get("pendingFolderBrowse") or []:
                browse_id = str(browse.get("id") or "")
                if not browse_id:
                    continue
                employee_sid = str(browse.get("employeeAdSid") or policy.get("employeeAdSid") or "")
                parent = str(browse.get("parentPath") or "")
                try:
                    if parent:
                        nodes, err = browse_children(
                            employee_sid=employee_sid,
                            parent_path=parent,
                            max_entries=int(browse.get("maxEntries") or 200),
                            timeout_seconds=float(browse.get("timeoutSeconds") or 8),
                        )
                    else:
                        nodes, err = resolve_known_folders(employee_sid)
                    client.folder_browse_result(browse_id, nodes=nodes_to_json(nodes), error=err)
                except Exception as browse_exc:  # noqa: BLE001
                    log.exception("folder_browse_failed id=%s", browse_id)
                    try:
                        client.folder_browse_result(browse_id, nodes=[], error=str(browse_exc)[:500])
                    except Exception:  # noqa: BLE001
                        pass

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
    if index is not None:
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
