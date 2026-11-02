from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select

from qka_api.smb import ACL_CAPTURED, AclSnapshot, EnumeratedFile
from qka_shared.models import SynologyFileInventory
from qka_shared.source_access import PathMode, grant_matches_file_type, grant_matches_path, under_prefix
from tests.conftest import SHARE_ID, SID_ALLOWED
from tests.test_source_access import _admin_headers
from tests.test_synology import _admin, _file, _gateway, _run_worker


def test_under_prefix_does_not_match_sibling_name():
    assert under_prefix(r"FINANCE\Q1.XLSX", "FINANCE")
    assert not under_prefix(r"FINANCEPRIVATE\Q1.XLSX", "FINANCE")
    assert under_prefix(r"EDUARDTESTCLOUD\ASSETS\A.XLSX", "EDUARDTESTCLOUD")
    assert not under_prefix(r"EDUARDTESTCLOUDEXTRA\A.XLSX", "EDUARDTESTCLOUD")


def test_include_and_exclude_subfolders():
    scope = SimpleNamespace(folder_prefix_normalized="EDUARDTESTCLOUD")
    grant_sub = SimpleNamespace(
        path_mode=PathMode.SELECTED,
        include_subfolders=True,
        selected_paths_json=json.dumps([r"EDUARDTESTCLOUD\ASSETS"]),
    )
    grant_exact = SimpleNamespace(
        path_mode=PathMode.SELECTED,
        include_subfolders=False,
        selected_paths_json=json.dumps([r"EDUARDTESTCLOUD\ASSETS"]),
    )
    nested = r"EDUARDTESTCLOUD\ASSETS\2024\FILE.XLSX"
    direct = r"EDUARDTESTCLOUD\ASSETS\FILE.XLSX"
    sibling = r"EDUARDTESTCLOUD\OTHER\FILE.XLSX"
    assert grant_matches_path(grant_sub, scope, nested)
    assert grant_matches_path(grant_sub, scope, direct)
    assert not grant_matches_path(grant_sub, scope, sibling)
    assert grant_matches_path(grant_exact, scope, direct)
    assert not grant_matches_path(grant_exact, scope, nested)
    assert grant_matches_file_type(SimpleNamespace(file_types_json='["excel"]'), "xlsx")
    assert not grant_matches_file_type(SimpleNamespace(file_types_json='["excel"]'), "pdf")


def test_seeded_inventory_is_a_lazy_hierarchy(client):
    headers = _admin_headers(client)
    tree = client.get(
        "/api/v1/access/source/folder-tree",
        headers=headers,
        params={"approvedShareId": str(SHARE_ID), "rootPrefix": ""},
    )
    assert tree.status_code == 200, tree.text
    roots = tree.json()
    assert roots
    assert all("children" not in node or node.get("children") in ([], None) or node.get("lazy") for node in roots)
    names = {node["label"] for node in roots}
    assert "EDUARDTESTCLOUD" in names or any(node["path"] == "EDUARDTESTCLOUD" for node in roots)

    children = client.get(
        "/api/v1/access/source/folder-children",
        headers=headers,
        params={"approvedShareId": str(SHARE_ID), "parentPath": "EDUARDTESTCLOUD"},
    )
    assert children.status_code == 200, children.text
    body = children.json()
    assert body["breadcrumb"][0]["path"] == "EDUARDTESTCLOUD"
    labels = {item["label"] for item in body["items"]}
    assert "Assets New Form.xlsx" in labels
    assert body["total"] >= 1
    assert len(body["items"]) <= body["pageSize"]

    found = client.get(
        "/api/v1/access/source/folder-search",
        headers=headers,
        params={"approvedShareId": str(SHARE_ID), "q": "Assets New"},
    )
    assert found.status_code == 200
    assert any("Assets New Form.xlsx" in item["label"] for item in found.json()["items"])


def test_folder_tree_empty_prefix_lists_all_share_roots(client):
    headers = _admin_headers(client)
    now = datetime.now(timezone.utc)
    with client.app.state.session_factory() as session:
        session.add(
            SynologyFileInventory(
                id=uuid.uuid4(),
                approved_share_id=SHARE_ID,
                relative_path="DOWNLOADS",
                relative_path_normalized="DOWNLOADS",
                file_name="DOWNLOADS",
                extension="",
                file_type_category=0,
                size_bytes=0,
                sync_status=0,
                acl_capture_status=0,
                is_present=True,
                is_directory=True,
                parent_path_normalized="",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    scoped = client.get(
        "/api/v1/access/source/folder-tree",
        headers=headers,
        params={"approvedShareId": str(SHARE_ID), "rootPrefix": "EDUARDTESTCLOUD"},
    )
    assert scoped.status_code == 200, scoped.text
    assert {node["label"] for node in scoped.json()} == {"EDUARDTESTCLOUD"}

    entire = client.get(
        "/api/v1/access/source/folder-tree",
        headers=headers,
        params={"approvedShareId": str(SHARE_ID), "rootPrefix": ""},
    )
    assert entire.status_code == 200, entire.text
    entire_labels = {node["label"] for node in entire.json()}
    assert "DOWNLOADS" in entire_labels
    assert "EDUARDTESTCLOUD" in entire_labels

    omitted = client.get(
        "/api/v1/access/source/folder-tree",
        headers=headers,
        params={"approvedShareId": str(SHARE_ID)},
    )
    assert omitted.status_code == 200, omitted.text
    assert {node["label"] for node in omitted.json()} == entire_labels


def test_sync_creates_folders_empty_dirs_and_preserves_incomplete(client):
    headers = _admin(client)
    gateway = _gateway(client)
    saved = client.post(
        "/api/v1/admin/synology/connections",
        json={
            "name": "Hierarchy NAS",
            "sharedFolderPath": r"\\192.168.110.11\eduard",
            "authMode": 1,
            "serviceAccountUserName": r".\qkai",
            "serviceAccountSecret": "nas-secret-never-echo",
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    connection_id = saved.json()["id"]
    client.post(f"/api/v1/admin/synology/connections/{connection_id}/test", headers=headers)

    empty = EnumeratedFile(
        relative_path=r"EDUARDTESTCLOUD\EmptyFolder",
        file_name="EmptyFolder",
        extension="",
        size_bytes=0,
        last_write_utc=datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc),
        acl=AclSnapshot(True, ACL_CAPTURED, [{"Identity": SID_ALLOWED, "Rights": "Read", "AccessControlType": "Allow"}], None),
        is_directory=True,
    )
    nested = _file(r"EDUARDTESTCLOUD\Assets\dup.txt", size=8)
    other_dup = _file(r"EDUARDTESTCLOUD\Other\dup.txt", size=9)
    gateway.files = [empty, nested, other_dup]
    gateway.contents = {
        r"EDUARDTESTCLOUD\ASSETS\DUP.TXT": b"alpha pineapple",
        r"EDUARDTESTCLOUD\OTHER\DUP.TXT": b"beta pineapple",
    }
    started = client.post(
        "/api/v1/admin/synology/sync/start",
        json={"connectionId": connection_id},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    _run_worker(client)

    children = client.get(
        "/api/v1/access/source/folder-children",
        headers=headers,
        params={"approvedShareId": str(SHARE_ID), "parentPath": "EDUARDTESTCLOUD"},
    ).json()
    labels = {item["label"] for item in children["items"]}
    assert "EmptyFolder" in labels
    assert "Assets" in labels
    assert "Other" in labels

    assets = client.get(
        "/api/v1/access/source/folder-children",
        headers=headers,
        params={"approvedShareId": str(SHARE_ID), "parentPath": r"EDUARDTESTCLOUD\ASSETS"},
    ).json()
    other = client.get(
        "/api/v1/access/source/folder-children",
        headers=headers,
        params={"approvedShareId": str(SHARE_ID), "parentPath": r"EDUARDTESTCLOUD\OTHER"},
    ).json()
    assert any(item["label"] == "dup.txt" for item in assets["items"])
    assert any(item["label"] == "dup.txt" for item in other["items"])
    assert assets["items"][0]["path"] != other["items"][0]["path"]

    listed = client.get(
        "/api/v1/admin/synology/inventory",
        params={"page": 1, "pageSize": 50, "connectionId": connection_id},
    ).json()
    assert all("." in (row["fileName"] or "") or row["extension"] for row in listed["items"] if row["fileName"] != "EmptyFolder")
    assert not any(row["fileName"] == "EmptyFolder" for row in listed["items"])

    with client.app.state.session_factory() as session:
        folders = session.execute(
            select(SynologyFileInventory).where(
                SynologyFileInventory.is_directory.is_(True),
                SynologyFileInventory.is_present.is_(True),
            )
        ).scalars().all()
        assert any(row.file_name == "EmptyFolder" for row in folders)

    gateway.files = [other_dup]
    gateway.raise_after = 0
    interrupted = client.post(
        "/api/v1/admin/synology/sync/start",
        json={"connectionId": connection_id},
        headers=headers,
    )
    assert interrupted.status_code == 200
    _run_worker(client)
    failed = client.get(f"/api/v1/admin/synology/sync/jobs/{interrupted.json()['id']}").json()
    assert failed["enumerationComplete"] is False
    assert failed["filesRemoved"] == 0
    still = client.get(
        "/api/v1/access/source/folder-children",
        headers=headers,
        params={"approvedShareId": str(SHARE_ID), "parentPath": r"EDUARDTESTCLOUD\ASSETS"},
    ).json()
    assert any(item["label"] == "dup.txt" for item in still["items"])
