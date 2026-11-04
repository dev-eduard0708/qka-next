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
from qka_api.services.network_probe import NetworkProbe, default_network_probe
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
            "agentStatusLabel": "Not Registered / Unknown",
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
        "agentStatusLabel": "Connected" if online else "Registered (offline)",
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


def workstation_dto(session: Session, ws: AdWorkstation) -> dict[str, Any]:
    employee_name = ""
    employee_email = ""
    if ws.assigned_qka_user_id:
        user = session.get(QkaUser, ws.assigned_qka_user_id)
        if user:
            employee_name = user.display_name or ""
            employee_email = user.contact_email or ""
    device = _match_desktop_device(session, ws)
    if device and ws.desktop_device_id != device.id:
        ws.desktop_device_id = device.id
    agent = _agent_fields(device)
    return {
        "id": str(ws.id),
        "connectionId": str(ws.connection_id),
        "computerName": ws.computer_name,
        "dnsHostname": ws.dns_hostname,
        "ipAddress": ws.resolved_ip or "",
        "ou": ws.ou_path,
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
        "deployAgent": {
            "enabled": False,
            "status": "not_implemented",
            "note": "Deploy Agent stays disabled until the real GPO/deployment mechanism is implemented.",
        },
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


def list_workstations(
    session: Session,
    *,
    search: str = "",
    ou: str = "",
    account_status: str = "",
    reachability: str = "",
    agent_status: str = "",
    assigned: str = "",
    include_missing: bool = False,
    include_servers: bool = False,
    sort: str = "computerName",
    sort_dir: str = "asc",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    q = select(AdWorkstation)
    if not include_missing:
        q = q.where(AdWorkstation.is_present.is_(True))
    if not include_servers:
        q = q.where(AdWorkstation.is_server_or_dc.is_(False))
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

    # Agent status filter requires post-filter (join desktop devices)
    rows = list(session.execute(q).scalars().all())
    if agent_status:
        filtered: list[AdWorkstation] = []
        for ws in rows:
            dto = workstation_dto(session, ws)
            if agent_status == "not_registered" and dto["agentStatus"] == "not_registered":
                filtered.append(ws)
            elif agent_status == "connected" and dto["agentStatus"] == "connected":
                filtered.append(ws)
            elif agent_status == "registered" and dto["agentStatus"] in (
                "connected",
                "registered_offline",
                "revoked",
            ):
                filtered.append(ws)
            elif agent_status == dto["agentStatus"]:
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
    items = [workstation_dto(session, ws) for ws in page_rows]

    present_q = select(func.count()).select_from(AdWorkstation).where(
        AdWorkstation.is_present.is_(True), AdWorkstation.is_server_or_dc.is_(False)
    )
    summary = {
        "totalPresent": int(session.execute(present_q).scalar_one()),
        "assigned": int(
            session.execute(
                select(func.count()).select_from(AdWorkstation).where(
                    AdWorkstation.is_present.is_(True),
                    AdWorkstation.is_server_or_dc.is_(False),
                    AdWorkstation.assigned_qka_user_id.is_not(None),
                )
            ).scalar_one()
        ),
        "reachable": int(
            session.execute(
                select(func.count()).select_from(AdWorkstation).where(
                    AdWorkstation.is_present.is_(True),
                    AdWorkstation.is_server_or_dc.is_(False),
                    AdWorkstation.reachability_status == REACH_REACHABLE,
                )
            ).scalar_one()
        ),
        "noReply": int(
            session.execute(
                select(func.count()).select_from(AdWorkstation).where(
                    AdWorkstation.is_present.is_(True),
                    AdWorkstation.is_server_or_dc.is_(False),
                    AdWorkstation.reachability_status == REACH_NO_REPLY,
                )
            ).scalar_one()
        ),
        "dnsFailed": int(
            session.execute(
                select(func.count()).select_from(AdWorkstation).where(
                    AdWorkstation.is_present.is_(True),
                    AdWorkstation.is_server_or_dc.is_(False),
                    AdWorkstation.reachability_status == REACH_DNS_FAILED,
                )
            ).scalar_one()
        ),
        "unchecked": int(
            session.execute(
                select(func.count()).select_from(AdWorkstation).where(
                    AdWorkstation.is_present.is_(True),
                    AdWorkstation.is_server_or_dc.is_(False),
                    AdWorkstation.reachability_status == REACH_UNCHECKED,
                )
            ).scalar_one()
        ),
    }
    ous = [
        r[0]
        for r in session.execute(
            select(AdWorkstation.ou_path)
            .where(
                AdWorkstation.is_present.is_(True),
                AdWorkstation.is_server_or_dc.is_(False),
                AdWorkstation.ou_path != "",
            )
            .distinct()
            .order_by(AdWorkstation.ou_path)
        ).all()
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "summary": summary,
        "ous": ous,
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
        if user is None or not user.login_enabled:
            raise WorkstationValidationError("Assigned employee must be an existing active QKA user.")
        # Prefer AD-linked users but do not invent ownership from computer name.
        ad_link = session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.qka_user_id == qka_user_id,
                ExternalIdentity.provider == 0,
            )
        ).scalar_one_or_none()
        if ad_link is None:
            raise WorkstationValidationError("Assign an AD-linked employee only.")
    ws.assigned_qka_user_id = qka_user_id
    ws.updated_at = _utc_now()
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
