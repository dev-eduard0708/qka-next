"""AD workstation inventory, reachability probes, and employee association."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from qka_api.ldap_client import LdapGateway
from qka_api.services import active_directory as ad_svc
from qka_api.services import agent_deployment as deploy_svc
from qka_api.services.network_probe import NetworkProbe, default_network_probe
from qka_shared.config import Settings, get_settings
from qka_shared.models import (
    REACH_DNS_FAILED,
    REACH_NO_REPLY,
    REACH_PROBE_ERROR,
    REACH_REACHABLE,
    REACH_UNCHECKED,
    AdConnection,
    AdWorkstation,
    AdWorkstationOuScope,
    DesktopDevice,
    DEVICE_ACTIVE,
    DEVICE_PAUSED,
    DEVICE_PENDING,
    DEVICE_REVOKED,
    ExternalIdentity,
    INDEX_ERROR,
    INDEX_IDLE,
    INDEX_PAUSED,
    INDEX_RUNNING,
    QkaUser,
)

MAX_BULK_PING = 50
DEFAULT_PING_CONCURRENCY = 8
DEFAULT_PING_TIMEOUT_MS = 2000


class WorkstationError(Exception):
    pass


class WorkstationValidationError(WorkstationError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ou_from_dn(dn: str) -> str:
    parts = [p.strip() for p in (dn or "").split(",") if p.strip()]
    if not parts:
        return ""
    # Drop CN=computer; keep OU=/DC= path
    rest = parts[1:] if parts[0].upper().startswith("CN=") else parts
    return ",".join(rest)


def _dn_in_scopes(dn: str, ou_dns: list[str]) -> bool:
    """True when DN is exactly a selected OU or a child of one."""
    needle = (dn or "").strip().lower()
    if not needle or not ou_dns:
        return False
    for ou in ou_dns:
        scope = (ou or "").strip().lower()
        if not scope:
            continue
        if needle == scope or needle.endswith("," + scope):
            return True
    return False


def _short_name(hostname: str) -> str:
    host = (hostname or "").strip().lower()
    if not host:
        return ""
    return host.split(".", 1)[0]


def _reach_label(status: int) -> str:
    return {
        REACH_UNCHECKED: "unchecked",
        REACH_DNS_FAILED: "dns_failed",
        REACH_REACHABLE: "reachable",
        REACH_NO_REPLY: "no_reply",
        REACH_PROBE_ERROR: "probe_error",
    }.get(status, "unchecked")


def _index_label(state: int) -> str:
    return {
        INDEX_IDLE: "idle",
        INDEX_RUNNING: "running",
        INDEX_PAUSED: "paused",
        INDEX_ERROR: "error",
    }.get(state, "idle")


def _agent_fields(device: DesktopDevice | None) -> dict[str, Any]:
    """Honest agent status — never claim Not Installed without reliable evidence."""
    if device is None:
        return {
            "agentStatus": "not_registered",
            "agentStatusLabel": "Not registered",
            "agentVersion": "",
            "lastHeartbeatAt": None,
            "indexingStatus": "unknown",
            "online": False,
            "desktopDeviceId": None,
        }
    if device.status == DEVICE_REVOKED:
        return {
            "agentStatus": "revoked",
            "agentStatusLabel": "Revoked",
            "agentVersion": device.agent_version or "",
            "lastHeartbeatAt": device.last_heartbeat_at.isoformat() if device.last_heartbeat_at else None,
            "indexingStatus": _index_label(device.indexing_state),
            "online": False,
            "desktopDeviceId": str(device.id),
        }
    online = False
    if device.last_heartbeat_at:
        age = (_utc_now() - device.last_heartbeat_at).total_seconds()
        online = age < 120 and device.status in (DEVICE_ACTIVE, DEVICE_PAUSED, DEVICE_PENDING)
    status = "connected" if online else "registered_offline"
    return {
        "agentStatus": status,
        "agentStatusLabel": "Online" if online else "Agent Registered",
        "agentVersion": device.agent_version or "",
        "lastHeartbeatAt": device.last_heartbeat_at.isoformat() if device.last_heartbeat_at else None,
        "indexingStatus": _index_label(device.indexing_state),
        "online": online,
        "desktopDeviceId": str(device.id),
    }


def _match_desktop_device(session: Session, ws: AdWorkstation) -> DesktopDevice | None:
    if ws.desktop_device_id:
        device = session.get(DesktopDevice, ws.desktop_device_id)
        if device and device.status != DEVICE_REVOKED:
            return device
    names = {
        (ws.computer_name or "").strip().lower(),
        _short_name(ws.dns_hostname),
        _short_name(ws.computer_name),
    }
    names.discard("")
    if not names:
        return None
    devices = session.execute(
        select(DesktopDevice).where(DesktopDevice.status != DEVICE_REVOKED)
    ).scalars().all()
    for device in devices:
        candidates = {
            (device.computer_name or "").strip().lower(),
            _short_name(device.computer_name),
            _short_name(device.domain_joined_name),
        }
        if names & candidates:
            return device
    return None


def _deploy_agent_fields(
    settings: Settings,
    ws: AdWorkstation,
    device: DesktopDevice | None,
    job_dto: dict | None,
    *,
    prereq: dict[str, Any] | None = None,
    pilot_member_sids: frozenset[str] | None = None,
) -> dict[str, Any]:
    from qka_api.services import gpo_engine

    prereq = prereq or deploy_svc.deployment_prerequisites(settings)
    has_sid = bool((ws.object_sid or "").strip())
    target_group = str(prereq.get("gpoTargetGroup") or gpo_engine.DEFAULT_TARGET_GROUP)
    members = (
        pilot_member_sids
        if pilot_member_sids is not None
        else gpo_engine.list_pilot_group_member_sids(target_group)
    )
    in_pilot = bool(
        has_sid
        and gpo_engine.computer_sid_in_pilot_group(
            ws.object_sid or "",
            target_group=target_group,
            member_sids=members,
        )
    )
    # Employee assignment is post-install; deploy needs SID + presence + pilot-group membership.
    can_select = bool(ws.is_present and ws.account_enabled and has_sid and in_pilot)
    job_status = str((job_dto or {}).get("status") or "")
    display_status = str((job_dto or {}).get("displayStatus") or "")
    # Active authorization jobs block Deploy; cancelled/failed are history and allow Ready again.
    active_job = display_status in {"pending", "in_progress"} or job_status in {
        "requested",
        "approved",
        "scheduled",
        "waiting_for_computer",
        "installing",
    }
    enabled = bool(prereq["deployEnabled"] and can_select and device is None and not active_job)
    if device is not None:
        status = "agent_registered"
    elif active_job:
        status = display_status or job_status
    elif enabled:
        # Prefer readiness over a prior cancelled/failed job label.
        status = "ready"
    elif job_status in {"failed", "cancelled"}:
        status = job_status
    else:
        status = "blocked"
    note = ""
    if not has_sid:
        note = "AD objectSid is required for machine-identity enrollment."
    elif device is not None:
        note = "Agent already registered for this workstation."
    elif not in_pilot:
        note = (
            f"Not a member of '{target_group}'. Use Add to Deployment Pilot in this page "
            f"(Super Admin), then Deploy Agent after readiness checks pass."
        )
    elif prereq["blockers"]:
        note = "; ".join(prereq["blockers"])
    else:
        note = prereq.get("note") or ""
    if in_pilot and enabled:
        membership_status = "ready_to_deploy"
    elif in_pilot and device is not None:
        membership_status = "in_pilot_agent_installed"
    elif in_pilot:
        membership_status = "in_pilot"
    else:
        membership_status = "not_in_pilot"
    dist_mode = str(prereq.get("taskDistributionMode") or "pilot_group")
    no_reboot = bool(prereq.get("noRebootCentralDeployReady"))
    if device is not None:
        task_readiness = "deployed_or_installed"
    elif not in_pilot:
        task_readiness = "not_in_pilot"
    elif no_reboot or dist_mode == "ou_computers":
        task_readiness = "task_available"
    elif active_job and display_status == "in_progress":
        task_readiness = "gpo_pending"
    elif in_pilot:
        task_readiness = "gpo_pending"
    else:
        task_readiness = "unknown"
    gpo_note = ""
    if in_pilot and device is None:
        if task_readiness == "task_available":
            gpo_note = (
                "Task Available (OU/Domain Computers distribution). Install still waits for "
                "Super Admin Deploy + SID token authorization — QKA will not reboot."
            )
        else:
            gpo_note = (
                "GPO Pending: pilot-group GpoApply may not deliver QKA-Agent-Install until the "
                "computer security context refreshes. QKA will not reboot. Pending jobs time out "
                "instead of staying Pending forever. Approve Enable-QkaNoRebootTaskDistribution.ps1 "
                "for fully centralized no-reboot task distribution."
            )
    return {
        "enabled": enabled,
        "status": status,
        "displayStatus": display_status or status,
        "note": note,
        "pilotGroupMember": in_pilot,
        "pilotGroup": target_group,
        "membershipStatus": membership_status,
        "taskReadiness": task_readiness,
        "taskDistributionMode": dist_mode,
        "gpoReadinessNote": gpo_note,
        "prerequisites": {
            "httpsConfigured": bool(prereq.get("httpsConfigured")),
            "gpoEngineAvailable": bool(prereq.get("gpoEngineAvailable")),
            "msiPresent": bool(prereq.get("msiPresent")),
            "tlsTrust": str(prereq.get("tlsTrust") or "blocked"),
            "machineIdentityEnrollmentReady": bool(prereq.get("machineIdentityEnrollmentReady")),
            "noRebootCentralDeployReady": no_reboot,
        },
    }


def workstation_dto(
    session: Session,
    ws: AdWorkstation,
    settings: Settings | None = None,
    *,
    prereq: dict[str, Any] | None = None,
    pilot_member_sids: frozenset[str] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    employee_name = ""
    employee_email = ""
    if ws.assigned_qka_user_id:
        user = session.get(QkaUser, ws.assigned_qka_user_id)
        if user:
            employee_name = user.display_name or ""
            employee_email = user.contact_email or ""
    device = None
    if ws.desktop_device_id:
        device = session.get(DesktopDevice, ws.desktop_device_id)
    if device is None:
        device = _match_desktop_device(session, ws)
    if device and ws.desktop_device_id != device.id:
        ws.desktop_device_id = device.id
    agent = _agent_fields(device)
    job = deploy_svc.latest_job_for_workstation(session, ws.id)
    has_heartbeat = bool(agent.get("lastHeartbeatAt"))
    job_payload = (
        deploy_svc.job_dto(
            job,
            ws,
            agent_online=bool(agent.get("online")),
            agent_status=str(agent.get("agentStatus") or "not_registered"),
            has_heartbeat=has_heartbeat,
        )
        if job is not None
        else None
    )
    if job_payload is None and agent.get("agentStatus") in {"connected", "registered_offline"}:
        # No deployment job — still expose Online/Offline/Installed from heartbeat evidence.
        display = deploy_svc.compute_display_status(
            job=None,
            agent_online=bool(agent.get("online")),
            agent_status=str(agent.get("agentStatus") or "not_registered"),
            has_heartbeat=has_heartbeat,
        )
        job_payload = {
            "id": None,
            "status": display,
            "statusCode": None,
            "displayStatus": display,
            "attemptCount": 0,
            "lastError": "",
            "lastResult": "",
            "msiVersion": "",
            "msiExitCode": None,
            "restartRequired": False,
            "engine": "",
            "enrollmentMode": "",
            "gpoTargetGroup": "",
            "desktopDeviceId": agent.get("desktopDeviceId"),
            "createdAt": None,
            "updatedAt": None,
            "approvedAt": None,
            "scheduledAt": None,
            "installStartedAt": None,
            "msiCompletedAt": None,
            "installedAt": None,
            "cancelledAt": None,
        }
    return {
        "id": str(ws.id),
        "connectionId": str(ws.connection_id),
        "computerName": ws.computer_name,
        "dnsHostname": ws.dns_hostname,
        "ipAddress": ws.resolved_ip or "",
        "distinguishedName": ws.distinguished_name or "",
        "ou": ws.ou_path,
        "ouBreadcrumb": _breadcrumb_for_ou_dn(ws.ou_path),
        "operatingSystem": ws.operating_system,
        "accountStatus": "enabled" if ws.account_enabled else "disabled",
        "accountEnabled": ws.account_enabled,
        "isPresent": ws.is_present,
        "isServerOrDc": ws.is_server_or_dc,
        "assignedEmployeeId": str(ws.assigned_qka_user_id) if ws.assigned_qka_user_id else None,
        "assignedEmployeeName": employee_name,
        "assignedEmployeeEmail": employee_email,
        "reachabilityStatus": _reach_label(ws.reachability_status),
        "reachabilityDetail": ws.reachability_detail,
        "reachabilityNote": (
            "No ICMP reply does not mean the computer is powered off."
            if ws.reachability_status == REACH_NO_REPLY
            else ""
        ),
        "lastCheckedAt": ws.last_checked_at.isoformat() if ws.last_checked_at else None,
        "lastSeenAt": ws.last_seen_at.isoformat() if ws.last_seen_at else None,
        "agentStatus": agent["agentStatus"],
        "agentStatusLabel": agent["agentStatusLabel"],
        "agentVersion": agent["agentVersion"],
        "lastHeartbeatAt": agent["lastHeartbeatAt"],
        "indexingStatus": agent["indexingStatus"],
        "online": agent["online"],
        "desktopDeviceId": agent["desktopDeviceId"],
        "objectGuid": ws.object_guid,
        "objectSid": ws.object_sid,
        "deployAgent": _deploy_agent_fields(
            settings,
            ws,
            device,
            job_payload,
            prereq=prereq,
            pilot_member_sids=pilot_member_sids,
        ),
        "deploymentJob": job_payload,
    }


def _active_connection(session: Session) -> AdConnection:
    row = session.execute(
        select(AdConnection)
        .where(AdConnection.connection_status == ad_svc.STATUS_CONNECTED)
        .order_by(AdConnection.updated_at.desc())
    ).scalars().first()
    if row is None:
        raise WorkstationValidationError(
            "No connected Active Directory connection. Configure Windows Integrated AD first."
        )
    return row


def list_sync_ou_options(
    session: Session,
    settings,
    gateway: LdapGateway,
    *,
    connection_id: uuid.UUID | None = None,
    parent_dn: str = "",
    search: str = "",
) -> dict[str, Any]:
    row = ad_svc.get_connection(session, connection_id) if connection_id else _active_connection(session)
    if row.connection_status == ad_svc.STATUS_DISABLED:
        raise WorkstationValidationError("Active Directory connection is disabled.")
    params = ad_svc._bind_params(session, settings, row)
    parent = (parent_dn or row.base_dn or "").strip()
    nodes = gateway.list_ous(params, parent_dn=parent, search=search)
    selected = {
        s.distinguished_name
        for s in session.execute(
            select(AdWorkstationOuScope).where(
                AdWorkstationOuScope.connection_id == row.id,
                AdWorkstationOuScope.selected.is_(True),
            )
        ).scalars().all()
    }
    saved = [
        {
            "name": s.name,
            "distinguishedName": s.distinguished_name,
            "selected": True,
        }
        for s in session.execute(
            select(AdWorkstationOuScope).where(
                AdWorkstationOuScope.connection_id == row.id,
                AdWorkstationOuScope.selected.is_(True),
            )
        ).scalars().all()
    ]
    return {
        "connectionId": str(row.id),
        "connectionName": row.connection_name,
        "baseDn": row.base_dn,
        "parentDn": parent,
        "includesChildOus": True,
        "note": (
            "Selecting an OU syncs every computer in that OU and all nested child OUs. "
            "Open a parent OU to choose more specific child OUs (for example exclude Suspend WorkStations)."
        ),
        "selectedItems": saved,
        "items": [
            {
                "name": n.name,
                "distinguishedName": n.distinguished_name,
                "selected": n.distinguished_name in selected,
            }
            for n in nodes
        ],
    }


def set_sync_ou_scope(
    session: Session,
    *,
    connection_id: uuid.UUID | None,
    selections: list[dict[str, Any]],
) -> dict[str, Any]:
    row = ad_svc.get_connection(session, connection_id) if connection_id else _active_connection(session)
    now = _utc_now()
    existing = {
        r.distinguished_name: r
        for r in session.execute(
            select(AdWorkstationOuScope).where(AdWorkstationOuScope.connection_id == row.id)
        ).scalars().all()
    }
    keep: set[str] = set()
    selected_count = 0
    for item in selections[:300]:
        dn = str(item.get("distinguishedName") or "").strip()
        if not dn:
            continue
        keep.add(dn)
        selected = bool(item.get("selected", True))
        if selected:
            selected_count += 1
        name = str(item.get("name") or dn.split(",")[0])
        if dn in existing:
            existing[dn].selected = selected
            existing[dn].name = name
            existing[dn].updated_at = now
        else:
            session.add(
                AdWorkstationOuScope(
                    id=uuid.uuid4(),
                    connection_id=row.id,
                    distinguished_name=dn,
                    name=name,
                    selected=selected,
                    created_at=now,
                    updated_at=now,
                )
            )
    for dn, scope_row in existing.items():
        if dn not in keep:
            scope_row.selected = False
            scope_row.updated_at = now
    session.flush()
    return {
        "connectionId": str(row.id),
        "selectedCount": selected_count,
        "items": [
            {
                "id": str(r.id),
                "name": r.name,
                "distinguishedName": r.distinguished_name,
                "selected": True,
            }
            for r in session.execute(
                select(AdWorkstationOuScope).where(
                    AdWorkstationOuScope.connection_id == row.id,
                    AdWorkstationOuScope.selected.is_(True),
                )
            ).scalars().all()
        ],
    }


def _resolve_sync_ou_dns(
    session: Session,
    row: AdConnection,
    ou_dns: list[str] | None,
) -> list[str]:
    explicit = [d.strip() for d in (ou_dns or []) if (d or "").strip()]
    if explicit:
        return explicit
    saved = session.execute(
        select(AdWorkstationOuScope).where(
            AdWorkstationOuScope.connection_id == row.id,
            AdWorkstationOuScope.selected.is_(True),
        )
    ).scalars().all()
    return [s.distinguished_name for s in saved if s.distinguished_name]


def sync_workstations(
    session: Session,
    settings,
    gateway: LdapGateway,
    *,
    connection_id: uuid.UUID | None = None,
    ou_dns: list[str] | None = None,
    include_servers: bool = False,
    persist_ou_scope: bool = True,
    actor_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    del actor_user_id  # reserved for future audit
    row = ad_svc.get_connection(session, connection_id) if connection_id else _active_connection(session)
    if row.connection_status == ad_svc.STATUS_DISABLED:
        raise WorkstationValidationError("Active Directory connection is disabled.")
    if row.connection_status != ad_svc.STATUS_CONNECTED:
        raise WorkstationValidationError("Active Directory connection is not connected.")

    resolved_ous = _resolve_sync_ou_dns(session, row, ou_dns)
    if not resolved_ous:
        raise WorkstationValidationError(
            "Select at least one Organizational Unit before syncing workstations."
        )
    if persist_ou_scope and ou_dns is not None:
        set_sync_ou_scope(
            session,
            connection_id=row.id,
            selections=[
                {"distinguishedName": dn, "name": dn.split(",")[0], "selected": True}
                for dn in resolved_ous
            ],
        )

    params = ad_svc._bind_params(session, settings, row)
    discovered = gateway.search_computers(
        params, ou_dns=resolved_ous, limit=5000, include_servers=include_servers
    )
    now = _utc_now()
    seen_guids: set[str] = set()
    created = 0
    updated = 0
    for rec in discovered:
        if not rec.object_guid:
            continue
        seen_guids.add(rec.object_guid)
        existing = session.execute(
            select(AdWorkstation).where(
                AdWorkstation.connection_id == row.id,
                AdWorkstation.object_guid == rec.object_guid,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = AdWorkstation(
                id=uuid.uuid4(),
                connection_id=row.id,
                object_guid=rec.object_guid,
                created_at=now,
                updated_at=now,
            )
            session.add(existing)
            created += 1
        else:
            updated += 1
        existing.object_sid = rec.object_sid
        existing.computer_name = rec.computer_name
        existing.dns_hostname = rec.dns_hostname
        existing.distinguished_name = rec.distinguished_name
        existing.ou_path = _ou_from_dn(rec.distinguished_name)
        existing.operating_system = rec.operating_system
        existing.account_enabled = rec.account_enabled
        existing.is_present = True
        existing.is_server_or_dc = rec.is_server_or_dc
        existing.last_seen_at = now
        existing.updated_at = now

    # Mark missing only within the OUs that were synced (do not delete history;
    # do not mark computers outside the selected OU scope as missing).
    missing = 0
    for ws in session.execute(
        select(AdWorkstation).where(
            AdWorkstation.connection_id == row.id,
            AdWorkstation.is_present.is_(True),
        )
    ).scalars().all():
        if ws.object_guid in seen_guids:
            continue
        if not include_servers and ws.is_server_or_dc:
            continue
        if not _dn_in_scopes(ws.distinguished_name, resolved_ous):
            continue
        ws.is_present = False
        ws.updated_at = now
        missing += 1

    session.flush()
    total = session.execute(
        select(func.count()).select_from(AdWorkstation).where(AdWorkstation.connection_id == row.id)
    ).scalar_one()
    present = session.execute(
        select(func.count()).select_from(AdWorkstation).where(
            AdWorkstation.connection_id == row.id, AdWorkstation.is_present.is_(True)
        )
    ).scalar_one()
    return {
        "connectionId": str(row.id),
        "discovered": len(discovered),
        "created": created,
        "updated": updated,
        "missingMarked": missing,
        "total": int(total),
        "present": int(present),
        "includeServers": include_servers,
        "syncedOus": resolved_ous,
        "syncedAt": now.isoformat(),
    }


def _saved_sync_ou_dns(session: Session, connection_id: uuid.UUID | None = None) -> list[str]:
    q = select(AdWorkstationOuScope).where(AdWorkstationOuScope.selected.is_(True))
    if connection_id is not None:
        q = q.where(AdWorkstationOuScope.connection_id == connection_id)
    else:
        try:
            conn = _active_connection(session)
            q = q.where(AdWorkstationOuScope.connection_id == conn.id)
        except WorkstationValidationError:
            return []
    return [r.distinguished_name for r in session.execute(q).scalars().all() if r.distinguished_name]


def _ou_label(dn: str) -> str:
    head = (dn or "").split(",")[0].strip()
    for prefix in ("OU=", "ou=", "DC=", "dc="):
        if head.startswith(prefix):
            return head[len(prefix) :]
    return head or dn


def _breadcrumb_for_ou_dn(dn: str) -> str:
    parts = [p.strip() for p in (dn or "").split(",") if p.strip()]
    labels: list[str] = []
    for part in reversed(parts):
        upper = part.upper()
        if upper.startswith("OU="):
            labels.append(part.split("=", 1)[1])
        elif upper.startswith("DC="):
            continue
    return " / ".join(labels) if labels else _ou_label(dn)


def build_inventory_ou_tree(session: Session) -> list[dict[str, Any]]:
    """Hierarchical OU tree from synchronized inventory only (no live LDAP)."""
    rows = session.execute(
        select(AdWorkstation.ou_path, AdWorkstation.distinguished_name).where(
            AdWorkstation.is_present.is_(True),
            AdWorkstation.is_server_or_dc.is_(False),
            AdWorkstation.ou_path != "",
        )
    ).all()
    # node_dn -> {label, count, children set}
    nodes: dict[str, dict[str, Any]] = {}
    for ou_path, dn in rows:
        path = (ou_path or "").strip()
        if not path:
            continue
        # Count this computer on its own OU and every ancestor OU.
        parts = [p.strip() for p in path.split(",") if p.strip()]
        for i in range(len(parts)):
            if not parts[i].upper().startswith("OU="):
                continue
            node_dn = ",".join(parts[i:])
            entry = nodes.setdefault(
                node_dn,
                {
                    "id": node_dn,
                    "label": _ou_label(node_dn),
                    "breadcrumb": _breadcrumb_for_ou_dn(node_dn),
                    "count": 0,
                    "childIds": set(),
                },
            )
            entry["count"] += 1
            # Link to parent OU if present
            for j in range(i + 1, len(parts)):
                if parts[j].upper().startswith("OU="):
                    parent_dn = ",".join(parts[j:])
                    parent = nodes.setdefault(
                        parent_dn,
                        {
                            "id": parent_dn,
                            "label": _ou_label(parent_dn),
                            "breadcrumb": _breadcrumb_for_ou_dn(parent_dn),
                            "count": 0,
                            "childIds": set(),
                        },
                    )
                    parent["childIds"].add(node_dn)
                    break

    def _to_tree(node_dn: str) -> dict[str, Any]:
        entry = nodes[node_dn]
        children = sorted(
            (_to_tree(cid) for cid in entry["childIds"] if cid in nodes),
            key=lambda n: n["label"].lower(),
        )
        payload: dict[str, Any] = {
            "id": entry["id"],
            "label": entry["label"],
            "breadcrumb": entry["breadcrumb"],
            "count": entry["count"],
        }
        # Omit empty children — Quasar QTree treats [] as expandable empty nodes.
        if children:
            payload["children"] = children
        return payload

    roots = []
    for node_dn, entry in nodes.items():
        # Root = no other node lists this as child
        is_child = any(node_dn in other["childIds"] for other in nodes.values())
        if not is_child:
            roots.append(_to_tree(node_dn))
    roots.sort(key=lambda n: n["label"].lower())
    return roots


def list_workstations(
    session: Session,
    *,
    settings: Settings | None = None,
    search: str = "",
    ou: str = "",
    scope_ou: str = "",
    filter_ou_dns: list[str] | None = None,
    include_children: bool = True,
    account_status: str = "",
    reachability: str = "",
    agent_status: str = "",
    assigned: str = "",
    deploy_status: str = "",
    include_missing: bool = False,
    include_servers: bool = False,
    scope_only: bool = False,
    sort: str = "computerName",
    sort_dir: str = "asc",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    """List AD workstations.

    Inventory visibility is independent of agent deployment / pilot GPO and of AD sync scope.
    ``filter_ou_dns`` is a *display* filter only — it never mutates sync OU selection.
    """
    settings = settings or get_settings()
    # Persist Timed Out transitions before DTO mapping so UI/polling see durable state.
    deploy_svc.reconcile_stale_deployment_jobs(session, settings)
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    active_sync_ous = _saved_sync_ou_dns(session)
    display_ous = [d.strip() for d in (filter_ou_dns or []) if (d or "").strip()]
    # Legacy single scopeOu still supported.
    selected_scope = (scope_ou or "").strip()
    if selected_scope and not display_ous:
        display_ous = [selected_scope]

    q = select(AdWorkstation)
    if not include_missing:
        q = q.where(AdWorkstation.is_present.is_(True))
    if not include_servers:
        q = q.where(AdWorkstation.is_server_or_dc.is_(False))
    # Exact leaf OU path (optional secondary refine).
    if ou:
        q = q.where(AdWorkstation.ou_path == ou)
    if account_status == "enabled":
        q = q.where(AdWorkstation.account_enabled.is_(True))
    elif account_status == "disabled":
        q = q.where(AdWorkstation.account_enabled.is_(False))
    reach_map = {
        "unchecked": REACH_UNCHECKED,
        "dns_failed": REACH_DNS_FAILED,
        "reachable": REACH_REACHABLE,
        "no_reply": REACH_NO_REPLY,
        "probe_error": REACH_PROBE_ERROR,
    }
    if reachability in reach_map:
        q = q.where(AdWorkstation.reachability_status == reach_map[reachability])
    if assigned == "yes":
        q = q.where(AdWorkstation.assigned_qka_user_id.is_not(None))
    elif assigned == "no":
        q = q.where(AdWorkstation.assigned_qka_user_id.is_(None))
    needle = (search or "").strip()
    if needle:
        like = f"%{needle}%"
        q = q.where(
            or_(
                AdWorkstation.computer_name.ilike(like),
                AdWorkstation.dns_hostname.ilike(like),
                AdWorkstation.resolved_ip.ilike(like),
                AdWorkstation.ou_path.ilike(like),
            )
        )

    rows = list(session.execute(q).scalars().all())
    if display_ous:
        if include_children:
            rows = [ws for ws in rows if _dn_in_scopes(ws.distinguished_name, display_ous)]
        else:
            wanted = {d.lower() for d in display_ous}
            rows = [ws for ws in rows if (ws.ou_path or "").lower() in wanted]
    elif scope_only and active_sync_ous:
        # Legacy multi-OU OR filter — UI no longer defaults to this.
        rows = [ws for ws in rows if _dn_in_scopes(ws.distinguished_name, active_sync_ous)]

    # Cached readiness + pilot SIDs once per list (not per row). Deploy submit still force-refreshes.
    from qka_api.services import gpo_engine

    list_prereq = deploy_svc.deployment_prerequisites(settings)
    pilot_member_sids = gpo_engine.list_pilot_group_member_sids(
        str(list_prereq.get("gpoTargetGroup") or gpo_engine.DEFAULT_TARGET_GROUP)
    )

    if agent_status or deploy_status:
        filtered: list[AdWorkstation] = []
        for ws in rows:
            dto = workstation_dto(
                session,
                ws,
                settings,
                prereq=list_prereq,
                pilot_member_sids=pilot_member_sids,
            )
            if agent_status:
                ok = False
                if agent_status == "not_registered" and dto["agentStatus"] == "not_registered":
                    ok = True
                elif agent_status == "connected" and dto["agentStatus"] == "connected":
                    ok = True
                elif agent_status == "registered" and dto["agentStatus"] in (
                    "connected",
                    "registered_offline",
                    "revoked",
                ):
                    ok = True
                elif agent_status == dto["agentStatus"]:
                    ok = True
                if not ok:
                    continue
            if deploy_status:
                job_status = ((dto.get("deploymentJob") or {}) or {}).get("status") or ""
                agent_deploy = ((dto.get("deployAgent") or {}) or {}).get("status") or ""
                needle_status = deploy_status.lower()
                if needle_status == "none":
                    if job_status:
                        continue
                elif needle_status == "ready":
                    if agent_deploy != "ready":
                        continue
                elif job_status != needle_status:
                    continue
            filtered.append(ws)
        rows = filtered

    reverse = sort_dir.lower() == "desc"
    sort_key = sort or "computerName"

    def _key(ws: AdWorkstation):
        if sort_key == "dnsHostname":
            return (ws.dns_hostname or "").lower()
        if sort_key == "ou":
            return (ws.ou_path or "").lower()
        if sort_key == "lastCheckedAt":
            return ws.last_checked_at or datetime.min.replace(tzinfo=timezone.utc)
        if sort_key == "ipAddress":
            return ws.resolved_ip or ""
        return (ws.computer_name or "").lower()

    rows.sort(key=_key, reverse=reverse)
    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]
    items = [
        workstation_dto(
            session,
            ws,
            settings,
            prereq=list_prereq,
            pilot_member_sids=pilot_member_sids,
        )
        for ws in page_rows
    ]

    summary = {
        "totalPresent": len(rows),
        "assigned": sum(1 for ws in rows if ws.assigned_qka_user_id is not None),
        "reachable": sum(1 for ws in rows if ws.reachability_status == REACH_REACHABLE),
        "noReply": sum(1 for ws in rows if ws.reachability_status == REACH_NO_REPLY),
        "dnsFailed": sum(1 for ws in rows if ws.reachability_status == REACH_DNS_FAILED),
        "unchecked": sum(1 for ws in rows if ws.reachability_status == REACH_UNCHECKED),
    }
    ous = sorted({(ws.ou_path or "") for ws in rows if ws.ou_path})
    sync_ou_options = [
        {
            "name": _ou_label(dn),
            "distinguishedName": dn,
            "breadcrumb": _breadcrumb_for_ou_dn(dn),
        }
        for dn in active_sync_ous
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "summary": summary,
        "ous": ous,
        "ouTree": build_inventory_ou_tree(session),
        "filterOuDns": display_ous,
        "includeChildren": bool(include_children),
        "scopeOu": display_ous[0] if len(display_ous) == 1 else None,
        "scopeOnly": bool(display_ous) or bool(scope_only and active_sync_ous),
        "activeSyncOus": sync_ou_options,
        "syncScopeNote": (
            "Configured AD sync OUs (inventory refresh only). Display filters never change this."
        ),
        "readinessCached": bool(list_prereq.get("cached")),
    }


def associate_employee(
    session: Session,
    workstation_id: uuid.UUID,
    *,
    qka_user_id: uuid.UUID | None,
) -> dict[str, Any]:
    ws = session.get(AdWorkstation, workstation_id)
    if ws is None:
        raise LookupError("Workstation not found.")
    if qka_user_id is not None:
        user = session.get(QkaUser, qka_user_id)
        # AD-enrolled employees are often login_enabled=False until Google/SSO is linked.
        # Workstation ownership only needs an active QkaUser with a linked AD identity.
        if user is None or user.status != 0 or user.is_super_admin:
            raise WorkstationValidationError("Assigned employee must be an existing active QKA user.")
        # Prefer AD-linked users but do not invent ownership from computer name.
        ad_link = session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.qka_user_id == qka_user_id,
                ExternalIdentity.provider == 0,
                ExternalIdentity.link_status == 2,
            )
        ).scalar_one_or_none()
        if ad_link is None:
            raise WorkstationValidationError("Assign an AD-linked employee only.")
    ws.assigned_qka_user_id = qka_user_id
    ws.updated_at = _utc_now()
    # Source ownership follows workstation assignment; preserve device folders/index history.
    device: DesktopDevice | None = None
    if ws.desktop_device_id:
        device = session.get(DesktopDevice, ws.desktop_device_id)
    if device is None:
        device = _match_desktop_device(session, ws)
    if device is not None and device.status != DEVICE_REVOKED:
        device.qka_user_id = qka_user_id
        device.updated_at = _utc_now()
        if qka_user_id is None:
            device.read_only_detail = (
                "Employee unassigned — indexed content restricted; approved folders preserved."
            )
        if ws.desktop_device_id != device.id:
            ws.desktop_device_id = device.id
    session.flush()
    return workstation_dto(session, ws)


def _probe_hostname(ws: AdWorkstation) -> str:
    return (ws.dns_hostname or ws.computer_name or "").strip()


def probe_workstation(
    session: Session,
    workstation_id: uuid.UUID,
    probe: NetworkProbe | None = None,
    *,
    timeout_ms: int = DEFAULT_PING_TIMEOUT_MS,
) -> dict[str, Any]:
    ws = session.get(AdWorkstation, workstation_id)
    if ws is None:
        raise LookupError("Workstation not found.")
    if not ws.is_present:
        raise WorkstationValidationError("Cannot probe a workstation marked missing from AD.")
    probe = probe or default_network_probe()
    host = _probe_hostname(ws)
    now = _utc_now()
    dns = probe.resolve_dns(host)
    ws.last_checked_at = now
    ws.updated_at = now
    if not dns.ok:
        ws.resolved_ip = ""
        ws.reachability_status = REACH_DNS_FAILED
        ws.reachability_detail = dns.detail
        session.flush()
        return workstation_dto(session, ws)

    ws.resolved_ip = dns.ip
    ping_target = dns.ip or host
    try:
        ping = probe.icmp_ping(ping_target, timeout_ms=timeout_ms)
    except Exception as exc:  # noqa: BLE001 — surface as probe_error
        ws.reachability_status = REACH_PROBE_ERROR
        ws.reachability_detail = f"Probe error: {exc}"
        session.flush()
        return workstation_dto(session, ws)

    if ping.replied:
        ws.reachability_status = REACH_REACHABLE
        ws.reachability_detail = f"{dns.detail}; {ping.detail}"
    else:
        ws.reachability_status = REACH_NO_REPLY
        ws.reachability_detail = f"{dns.detail}; {ping.detail}"
    session.flush()
    return workstation_dto(session, ws)


def probe_workstations_bulk(
    session: Session,
    workstation_ids: list[uuid.UUID],
    probe: NetworkProbe | None = None,
    *,
    timeout_ms: int = DEFAULT_PING_TIMEOUT_MS,
    concurrency: int = DEFAULT_PING_CONCURRENCY,
) -> dict[str, Any]:
    if not workstation_ids:
        raise WorkstationValidationError("Select one or more authorized workstations to probe.")
    if len(workstation_ids) > MAX_BULK_PING:
        raise WorkstationValidationError(f"Bulk ping is limited to {MAX_BULK_PING} workstations per request.")

    # Only probe IDs that exist in inventory (authorized domain computers).
    rows = session.execute(
        select(AdWorkstation).where(
            AdWorkstation.id.in_(workstation_ids),
            AdWorkstation.is_present.is_(True),
        )
    ).scalars().all()
    by_id = {r.id: r for r in rows}
    missing = [str(i) for i in workstation_ids if i not in by_id]
    if missing:
        raise WorkstationValidationError(
            "One or more selected IDs are not present authorized workstations. "
            "Network probes only run against synced AD computer inventory."
        )

    probe = probe or default_network_probe()
    concurrency = max(1, min(int(concurrency), DEFAULT_PING_CONCURRENCY))
    timeout_ms = max(200, min(int(timeout_ms), 10000))

    # Resolve DNS + ping outside the ORM session lock via thread pool; apply results after.
    def _run(ws_id: uuid.UUID) -> tuple[uuid.UUID, str, str, int, str]:
        ws = by_id[ws_id]
        host = _probe_hostname(ws)
        dns = probe.resolve_dns(host)
        if not dns.ok:
            return ws_id, "", dns.detail, REACH_DNS_FAILED, dns.detail
        try:
            ping = probe.icmp_ping(dns.ip or host, timeout_ms=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            return ws_id, dns.ip, f"{dns.detail}; Probe error: {exc}", REACH_PROBE_ERROR, str(exc)
        if ping.replied:
            return ws_id, dns.ip, f"{dns.detail}; {ping.detail}", REACH_REACHABLE, ping.detail
        return ws_id, dns.ip, f"{dns.detail}; {ping.detail}", REACH_NO_REPLY, ping.detail

    results_meta: list[dict[str, Any]] = []
    now = _utc_now()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_run, wid): wid for wid in workstation_ids}
        for fut in as_completed(futures):
            ws_id, ip, detail, status, _raw = fut.result()
            ws = by_id[ws_id]
            ws.resolved_ip = ip
            ws.reachability_status = status
            ws.reachability_detail = detail
            ws.last_checked_at = now
            ws.updated_at = now
            results_meta.append(
                {
                    "id": str(ws_id),
                    "computerName": ws.computer_name,
                    "reachabilityStatus": _reach_label(status),
                    "ipAddress": ip,
                    "detail": detail,
                }
            )
    session.flush()
    results_meta.sort(key=lambda r: r["computerName"].lower())
    return {
        "processed": len(results_meta),
        "concurrency": concurrency,
        "timeoutMs": timeout_ms,
        "results": results_meta,
        "items": [workstation_dto(session, by_id[wid]) for wid in workstation_ids],
        "note": "No ICMP reply does not mean a computer is powered off.",
    }
