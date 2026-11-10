"""Desktop agent hardware/volume inventory upsert + folder browse cache."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from qka_shared.models import DesktopDevice, DesktopDeviceInventory, DesktopFolderCache

HARDWARE_STALE = timedelta(hours=25)
VOLUMES_STALE = timedelta(minutes=45)
FOLDER_CACHE_STALE = timedelta(minutes=45)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _loads(raw: str, fallback):
    try:
        return json.loads(raw or "") or fallback
    except json.JSONDecodeError:
        return fallback


def _hash_payload(obj) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _norm_parent(path: str) -> str:
    p = (path or "").strip().replace("/", "\\")
    if len(p) == 2 and p[1] == ":":
        return p + "\\"
    return p.rstrip("\\") if p not in {"", "\\"} else ""


def inventory_due(session: Session, device: DesktopDevice) -> dict:
    row = session.get(DesktopDeviceInventory, device.id)
    now = _now()
    force = bool(row and row.refresh_requested_at)
    hardware_due = True
    volumes_due = True
    if row and row.hardware_synced_at and not force:
        hardware_due = (now - row.hardware_synced_at) >= HARDWARE_STALE
    if row and row.volumes_synced_at and not force:
        volumes_due = (now - row.volumes_synced_at) >= VOLUMES_STALE
    return {
        "hardwareDue": hardware_due,
        "volumesDue": volumes_due,
        "forceRefresh": force,
        "hasInventory": row is not None,
    }


def upsert_inventory(
    session: Session,
    device: DesktopDevice,
    *,
    payload: dict,
) -> dict:
    """Upsert inventory from agent. Never modifies NTFS ACLs or ownership."""
    now = _now()
    row = session.get(DesktopDeviceInventory, device.id)
    created = False
    if row is None:
        row = DesktopDeviceInventory(
            device_id=device.id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        created = True

    hardware = payload.get("hardware") or {}
    volumes = payload.get("volumes") or []
    disks = payload.get("disks") or []
    known = payload.get("knownFolders") or []
    include_hardware = bool(payload.get("includeHardware", True))
    include_volumes = bool(payload.get("includeVolumes", True))

    row.workstation_id = device.workstation_id
    row.agent_version = str(payload.get("agentVersion") or device.agent_version or "")[:64]
    changed = False

    if include_hardware:
        hw_obj = {
            "hostname": str(hardware.get("hostname") or "")[:256],
            "domainName": str(hardware.get("domainName") or "")[:256],
            "osEdition": str(hardware.get("osEdition") or "")[:256],
            "osVersion": str(hardware.get("osVersion") or "")[:128],
            "osBuild": str(hardware.get("osBuild") or "")[:64],
            "manufacturer": str(hardware.get("manufacturer") or "")[:256],
            "model": str(hardware.get("model") or "")[:256],
            "serialNumber": str(hardware.get("serialNumber") or "")[:256],
            "cpuModel": str(hardware.get("cpuModel") or "")[:512],
            "cpuPhysicalCores": int(hardware.get("cpuPhysicalCores") or 0),
            "cpuLogicalProcessors": int(hardware.get("cpuLogicalProcessors") or 0),
            "ramBytes": int(hardware.get("ramBytes") or 0),
            "disks": disks,
        }
        hw_hash = _hash_payload(hw_obj)
        if hw_hash != row.hardware_hash:
            changed = True
            row.hostname = hw_obj["hostname"]
            row.domain_name = hw_obj["domainName"]
            row.os_edition = hw_obj["osEdition"]
            row.os_version = hw_obj["osVersion"]
            row.os_build = hw_obj["osBuild"]
            row.manufacturer = hw_obj["manufacturer"]
            row.model = hw_obj["model"]
            row.serial_number = hw_obj["serialNumber"]
            row.cpu_model = hw_obj["cpuModel"]
            row.cpu_physical_cores = hw_obj["cpuPhysicalCores"]
            row.cpu_logical_processors = hw_obj["cpuLogicalProcessors"]
            row.ram_bytes = hw_obj["ramBytes"]
            row.disks_json = json.dumps(disks, ensure_ascii=True)
            row.hardware_hash = hw_hash
        row.hardware_synced_at = now

    if include_volumes:
        vol_obj = {"volumes": volumes, "knownFolders": known}
        vol_hash = _hash_payload(vol_obj)
        if vol_hash != row.volumes_hash:
            changed = True
            row.volumes_json = json.dumps(volumes, ensure_ascii=True)
            row.known_folders_json = json.dumps(known, ensure_ascii=True)
            row.volumes_hash = vol_hash
        row.volumes_synced_at = now
        # Seed root folder cache from navigation roots (volumes + known folders).
        root_nodes = _roots_as_nodes(volumes, known)
        upsert_folder_cache(session, device_id=device.id, parent_path="", nodes=root_nodes, error="")

    if row.refresh_requested_at is not None:
        row.refresh_requested_at = None
    row.updated_at = now
    session.flush()
    return {
        "ok": True,
        "created": created,
        "changed": changed,
        "inventory": inventory_dto(session, device),
    }


def request_inventory_refresh(session: Session, device_id: uuid.UUID) -> dict:
    device = session.get(DesktopDevice, device_id)
    if device is None:
        raise LookupError("Device was not found.")
    now = _now()
    row = session.get(DesktopDeviceInventory, device.id)
    if row is None:
        row = DesktopDeviceInventory(device_id=device.id, created_at=now, updated_at=now)
        session.add(row)
    row.refresh_requested_at = now
    row.workstation_id = device.workstation_id
    row.updated_at = now
    session.flush()
    return {"ok": True, "refreshRequestedAt": now.isoformat()}


def upsert_folder_cache(
    session: Session,
    *,
    device_id: uuid.UUID,
    parent_path: str,
    nodes: list[dict],
    error: str = "",
) -> dict:
    now = _now()
    parent = _norm_parent(parent_path)
    content_hash = _hash_payload({"nodes": nodes, "error": error or ""})
    row = session.execute(
        select(DesktopFolderCache).where(
            DesktopFolderCache.device_id == device_id,
            DesktopFolderCache.parent_path == parent,
        )
    ).scalar_one_or_none()
    if row is None:
        row = DesktopFolderCache(
            id=uuid.uuid4(),
            device_id=device_id,
            parent_path=parent,
            nodes_json=json.dumps(nodes, ensure_ascii=True),
            error=(error or "")[:2000],
            content_hash=content_hash,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        if row.content_hash != content_hash:
            row.nodes_json = json.dumps(nodes, ensure_ascii=True)
            row.error = (error or "")[:2000]
            row.content_hash = content_hash
        row.synced_at = now
        row.updated_at = now
    session.flush()
    return folder_cache_dto(row)


def get_folder_cache(session: Session, *, device_id: uuid.UUID, parent_path: str = "") -> dict | None:
    parent = _norm_parent(parent_path)
    row = session.execute(
        select(DesktopFolderCache).where(
            DesktopFolderCache.device_id == device_id,
            DesktopFolderCache.parent_path == parent,
        )
    ).scalar_one_or_none()
    return folder_cache_dto(row) if row else None


def inventory_dto(session: Session, device: DesktopDevice) -> dict | None:
    row = session.get(DesktopDeviceInventory, device.id)
    if row is None:
        return None
    now = _now()
    hw_age = (now - row.hardware_synced_at) if row.hardware_synced_at else None
    vol_age = (now - row.volumes_synced_at) if row.volumes_synced_at else None
    return {
        "deviceId": str(device.id),
        "workstationId": str(row.workstation_id) if row.workstation_id else None,
        "hostname": row.hostname or device.computer_name,
        "domainName": row.domain_name or device.domain_joined_name,
        "osEdition": row.os_edition,
        "osVersion": row.os_version,
        "osBuild": row.os_build,
        "manufacturer": row.manufacturer,
        "model": row.model,
        "serialNumber": row.serial_number,
        "cpuModel": row.cpu_model,
        "cpuPhysicalCores": int(row.cpu_physical_cores or 0),
        "cpuLogicalProcessors": int(row.cpu_logical_processors or 0),
        "ramBytes": int(row.ram_bytes or 0),
        "agentVersion": row.agent_version or device.agent_version,
        "disks": _loads(row.disks_json, []),
        "volumes": _loads(row.volumes_json, []),
        "knownFolders": _loads(row.known_folders_json, []),
        "hardwareSyncedAt": row.hardware_synced_at.isoformat() if row.hardware_synced_at else None,
        "volumesSyncedAt": row.volumes_synced_at.isoformat() if row.volumes_synced_at else None,
        "hardwareStale": hw_age is None or hw_age >= HARDWARE_STALE,
        "volumesStale": vol_age is None or vol_age >= VOLUMES_STALE,
        "refreshRequestedAt": row.refresh_requested_at.isoformat() if row.refresh_requested_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def folder_cache_dto(row: DesktopFolderCache) -> dict:
    now = _now()
    age = now - row.synced_at if row.synced_at else FOLDER_CACHE_STALE
    return {
        "deviceId": str(row.device_id),
        "parentPath": row.parent_path,
        "nodes": _loads(row.nodes_json, []),
        "error": row.error or "",
        "syncedAt": row.synced_at.isoformat() if row.synced_at else None,
        "stale": age >= FOLDER_CACHE_STALE,
    }


def browse_roots_dto(session: Session, device: DesktopDevice) -> dict:
    inv = inventory_dto(session, device)
    cached = get_folder_cache(session, device_id=device.id, parent_path="")
    volumes = (inv or {}).get("volumes") or []
    known = (inv or {}).get("knownFolders") or []
    nodes = (cached or {}).get("nodes") if cached else None
    if not nodes:
        nodes = _roots_as_nodes(volumes, known)
    return {
        "deviceId": str(device.id),
        "online": False,  # filled by caller
        "inventory": inv,
        "nodes": nodes,
        "syncedAt": (cached or {}).get("syncedAt") or (inv or {}).get("volumesSyncedAt"),
        "stale": bool((cached or {}).get("stale", True) if cached else True),
        "cacheHit": cached is not None,
    }


def _roots_as_nodes(volumes: list, known: list) -> list[dict]:
    nodes: list[dict] = []
    for v in volumes:
        letter = str(v.get("letter") or v.get("name") or "").strip()
        root = str(v.get("rootPath") or v.get("path") or "").strip()
        if not root and letter:
            root = letter if letter.endswith("\\") else f"{letter.rstrip(':')}:"
            if not root.endswith("\\"):
                root += "\\"
        if not root:
            continue
        label = str(v.get("label") or "").strip()
        name = letter or root.rstrip("\\")
        if label:
            name = f"{name.rstrip(':')} ({label})"
        nodes.append(
            {
                "name": name,
                "path": root,
                "kind": "volume",
                "accessible": bool(v.get("accessible", True)),
                "hasChildren": bool(v.get("accessible", True)),
                "detail": _volume_detail(v),
                "capacityBytes": int(v.get("capacityBytes") or 0),
                "freeBytes": int(v.get("freeBytes") or 0),
                "usedBytes": int(v.get("usedBytes") or max(0, int(v.get("capacityBytes") or 0) - int(v.get("freeBytes") or 0))),
            }
        )
    for k in known:
        nodes.append(
            {
                "name": str(k.get("name") or "Folder"),
                "path": str(k.get("path") or ""),
                "kind": str(k.get("kind") or "known_folder"),
                "accessible": bool(k.get("accessible", False)),
                "hasChildren": bool(k.get("hasChildren", False)),
                "detail": str(k.get("detail") or ""),
            }
        )
    return nodes


def _volume_detail(v: dict) -> str:
    cap = int(v.get("capacityBytes") or 0)
    free = int(v.get("freeBytes") or 0)
    if cap <= 0:
        return str(v.get("fileSystem") or "")
    used_pct = int(round(((cap - free) / cap) * 100)) if cap else 0
    return f"{_fmt_bytes(cap - free)} used / {_fmt_bytes(cap)} ({used_pct}%) · {v.get('fileSystem') or ''}".strip(" ·")


def _fmt_bytes(n: int) -> str:
    val = float(max(0, int(n or 0)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024 or unit == "TB":
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{n} B"
