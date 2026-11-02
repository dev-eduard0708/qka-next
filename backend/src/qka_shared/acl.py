from __future__ import annotations

import json
from enum import IntEnum

from qka_shared.models import SynologyFileInventory


class SourceAclState(IntEnum):
    MISSING = 0
    DENIED = 1
    ALLOWED = 2


class AclCaptureStatus(IntEnum):
    MISSING = 0
    CAPTURED = 1
    ACCESS_DENIED = 2


def evaluate(inventory: SynologyFileInventory, allow_principal_contains: str) -> SourceAclState:
    if inventory.acl_capture_status != AclCaptureStatus.CAPTURED or not inventory.acl_snapshot_json:
        return SourceAclState.MISSING
    needle = (allow_principal_contains or "").strip()
    if not needle:
        return SourceAclState.MISSING
    try:
        entries = json.loads(inventory.acl_snapshot_json)
    except json.JSONDecodeError:
        return SourceAclState.MISSING
    if not isinstance(entries, list) or not entries:
        return SourceAclState.MISSING

    deny_hit = False
    allow_hit = False
    for entry in entries:
        identity = str(entry.get("Identity") or "")
        if needle.lower() not in identity.lower():
            continue
        rights = str(entry.get("Rights") or "")
        readish = any(
            token.lower() in rights.lower()
            for token in ("Read", "ListDirectory", "ReadAndExecute", "FullControl", "Modify")
        )
        if not readish:
            continue
        control = str(entry.get("AccessControlType") or "")
        if control.lower() == "deny":
            deny_hit = True
        elif control.lower() == "allow":
            allow_hit = True
    if deny_hit:
        return SourceAclState.DENIED
    return SourceAclState.ALLOWED if allow_hit else SourceAclState.DENIED


def evaluate_needles(inventory: SynologyFileInventory, needles: list[str]) -> SourceAclState:
    cleaned = [item.strip() for item in needles if item and item.strip()]
    if inventory.acl_capture_status != AclCaptureStatus.CAPTURED or not inventory.acl_snapshot_json:
        return SourceAclState.MISSING
    if not cleaned:
        return SourceAclState.MISSING
    if any(evaluate(inventory, needle) == SourceAclState.ALLOWED for needle in cleaned):
        return SourceAclState.ALLOWED
    return SourceAclState.DENIED
