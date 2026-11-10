"""Agent remote deployment: prerequisites, GPO scope, machine-identity enroll, jobs."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from qka_api.services import agent_deployment as deploy_svc
from qka_api.services import gpo_engine
from qka_api.services.network_probe import PingResult
from qka_shared.config import Settings
from qka_shared.models import DEPLOY_FAILED, AgentDeploymentJob
from tests.conftest import ALLOWED_USER, auth_cookies
from tests.test_workstations import _admin, _ensure_ad_connection, _install

# Fake LDAP SIDs from tests/fake_ldap.py
PILOT_SID = "S-1-5-21-1-2-3-3001"
FINANCE_SID = "S-1-5-21-1-2-3-3002"
# Real approved pilot (documentation / live AD) — not in fake LDAP inventory.
WSIT02_SID = "S-1-5-21-3770028088-2973123715-664028206-1527"


def _sync_workstations(client, headers) -> list[dict]:
    conn_id = _ensure_ad_connection(client, headers)
    ou = "OU=Workstations,DC=qehc,DC=edu,DC=sa"
    sync = client.post(
        "/api/v1/admin/workstations/sync",
        headers=headers,
        json={"connectionId": conn_id, "ouDns": [ou], "includeServers": False},
    )
    assert sync.status_code == 200, sync.text
    listed = client.get("/api/v1/admin/workstations?scopeOnly=false", headers=headers)
    assert listed.status_code == 200
    return listed.json()["items"]


def test_gpo_manifest_rejects_empty_and_limits_bulk():
    with patch.object(gpo_engine, "probe_gpo_engine") as probe:
        probe.return_value = gpo_engine.GpoEngineStatus(
            available=True,
            reason="ok",
            target_group="QKA-Agent-Pilot-Computers",
            msi_path=str(Path(r"C:\fake\QKA-Agent.msi")),
            msi_present=True,
            gpmc_present=True,
        )
        try:
            gpo_engine.build_gpo_package_manifest(
                computer_names=[],
                server_base_url="https://agent.qehc.internal",
                msi_path=r"C:\fake\QKA-Agent.msi",
                target_group="QKA-Agent-Pilot-Computers",
            )
            assert False, "expected empty selection rejection"
        except ValueError as exc:
            assert "at least one" in str(exc).lower()

        try:
            gpo_engine.build_gpo_package_manifest(
                computer_names=[f"PC{i}" for i in range(51)],
                server_base_url="https://agent.qehc.internal",
                msi_path=r"C:\fake\QKA-Agent.msi",
                target_group="QKA-Agent-Pilot-Computers",
            )
            assert False, "expected bulk limit"
        except ValueError as exc:
            assert "50" in str(exc)

        manifest = gpo_engine.build_gpo_package_manifest(
            computer_names=["PILOT-PC", "FINANCE-01"],
            server_base_url="https://agent.qehc.internal",
            msi_path=r"C:\fake\QKA-Agent.msi",
            target_group="QKA-Agent-Pilot-Computers",
        )
        assert manifest["scopeRule"] == "computer_group_only"
        assert "entire_ou" in manifest["forbidden"]
        assert manifest["computers"] == ["PILOT-PC", "FINANCE-01"]
        assert "/qn" in manifest["silentArgs"]


def test_prerequisites_block_without_https_and_gpo(client):
    _install(client)
    headers = _admin(client)
    prereq = client.get("/api/v1/admin/workstations/deploy/prerequisites", headers=headers)
    assert prereq.status_code == 200, prereq.text
    body = prereq.json()
    assert body["deployEnabled"] is False
    assert body["realPcDeployAllowed"] is False
    assert body["blockers"]
    assert body["confirmationPhrase"] == deploy_svc.CONFIRM_PHRASE
    # Local GPMC absence must never appear as a deploy blocker.
    joined = " ".join(body["blockers"]).lower()
    assert "gpmc/get-gpo is not on this qka" not in joined
    assert not any(b.lower().startswith("on a gpmc host") for b in body["blockers"])


def test_gpo_hard_reasons_exclude_gpmc_note():
    status = gpo_engine.probe_gpo_engine(msi_unc="")
    assert all(not r.startswith("NOTE:") for r in status.hard_reasons)
    assert all("On a GPMC host" not in r for r in status.hard_reasons)
    assert any("MSI UNC" in r for r in status.hard_reasons)
    from qka_shared.config import get_settings

    pr = deploy_svc.deployment_prerequisites(get_settings())
    assert not any(b.lower().startswith("on a gpmc host") for b in pr["blockers"])
    assert not any("gpmc/get-gpo is not on this qka" in b.lower() for b in pr["blockers"])


def test_deploy_rejected_when_prerequisites_fail(client):
    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    pilot = next(i for i in items if i["computerName"] == "PILOT-PC")
    client.put(
        f"/api/v1/admin/workstations/{pilot['id']}/employee",
        headers=headers,
        json={"qkaUserId": str(ALLOWED_USER)},
    )
    denied = client.post(
        "/api/v1/admin/workstations/deploy",
        headers=headers,
        json={
            "workstationIds": [pilot["id"]],
            "confirmation": deploy_svc.CONFIRM_PHRASE,
            "approve": True,
        },
    )
    assert denied.status_code == 400
    assert "Deployment blocked" in denied.json()["detail"]


def test_unauthorized_deploy_rejected(client):
    _install(client)
    response = client.post(
        "/api/v1/admin/workstations/deploy",
        cookies=auth_cookies(ALLOWED_USER),
        json={"workstationIds": [str(uuid.uuid4())], "confirmation": deploy_svc.CONFIRM_PHRASE},
    )
    assert response.status_code == 403


def test_selected_and_bulk_deploy_jobs_when_ready(client, tmp_path):
    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    pilot = next(i for i in items if i["computerName"] == "PILOT-PC")
    finance = next(i for i in items if i["computerName"] == "FINANCE-01")
    for row in (pilot, finance):
        assert (
            client.put(
                f"/api/v1/admin/workstations/{row['id']}/employee",
                headers=headers,
                json={"qkaUserId": str(ALLOWED_USER)},
            ).status_code
            == 200
        )

    msi = tmp_path / "QKA-Agent.msi"
    msi.write_bytes(b"MZ-fake")
    ready = gpo_engine.GpoEngineStatus(
        available=True,
        reason="ok",
        target_group="QKA-Agent-Pilot-Computers",
        msi_path=str(msi),
        msi_present=True,
        gpmc_present=True,
    )
    settings = Settings(agent_public_base_url="https://agent.qehc.internal")

    with (
        patch.object(gpo_engine, "probe_gpo_engine", return_value=ready),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})),
        patch.object(deploy_svc, "deployment_prerequisites") as prereq_fn,
    ):
        prereq_fn.return_value = {
            **deploy_svc.deployment_prerequisites(settings),
            "deployEnabled": True,
            "httpsConfigured": True,
            "httpsEndpoint": "https://agent.qehc.internal",
            "blockers": [],
            "gpoEngineAvailable": True,
            "msiPresent": True,
            "maxBulk": 50,
            "gpoTargetGroup": "QKA-Agent-Pilot-Computers",
        }
        # Wrong confirmation
        bad = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={"workstationIds": [pilot["id"]], "confirmation": "YES", "approve": True},
        )
        assert bad.status_code == 400

        # Single selected PC (pilot-group member by SID)
        one = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [pilot["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
        assert one.status_code == 200, one.text
        assert one.json()["realPcDeployExecuted"] is False
        assert one.json()["jobs"][0]["status"] == "scheduled"
        assert one.json()["jobs"][0]["machineEnrollmentToken"]
        assert one.json()["gpoManifest"]["computers"] == ["PILOT-PC"]
        job_id = one.json()["jobs"][0]["id"]

        # Duplicate active job rejected
        dup = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [pilot["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
        assert dup.status_code == 400
        assert "active deployment" in dup.json()["detail"].lower()

        # Cancel then retry path
        cancel = client.post(f"/api/v1/admin/workstations/deploy/{job_id}/cancel", headers=headers)
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

        # Non-pilot SID rejected even when global prereqs are ready — no job/token created.
        denied = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [finance["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
        assert denied.status_code == 400
        assert "not authorized for pilot deployment" in denied.json()["detail"].lower()
        assert FINANCE_SID not in denied.json()["detail"]  # do not leak unrelated internals needlessly

        # Mixed batch with one unauthorized PC rejects entirely (no partial tokens).
        mixed = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [pilot["id"], finance["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
        assert mixed.status_code == 400
        assert "FINANCE-01" in mixed.json()["detail"]


def test_deploy_without_employee_then_enroll_unassigned(client, tmp_path):
    """Deploy/install must not require employee assignment; device registers unassigned."""
    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    pilot = next(i for i in items if i["computerName"] == "PILOT-PC")
    # Intentionally leave workstation employee unassigned.

    msi = tmp_path / "QKA-Agent.msi"
    msi.write_bytes(b"MZ")
    ready = gpo_engine.GpoEngineStatus(
        available=True,
        reason="ok",
        target_group="QKA-Agent-Pilot-Computers",
        msi_path=str(msi),
        msi_present=True,
        gpmc_present=True,
    )
    settings = Settings(agent_public_base_url="https://agent.qehc.internal")
    with (
        patch.object(gpo_engine, "probe_gpo_engine", return_value=ready),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})),
        patch.object(deploy_svc, "deployment_prerequisites") as prereq_fn,
    ):
        prereq_fn.return_value = {
            **deploy_svc.deployment_prerequisites(settings),
            "deployEnabled": True,
            "httpsConfigured": True,
            "httpsEndpoint": "https://agent.qehc.internal",
            "blockers": [],
            "gpoEngineAvailable": True,
            "msiPresent": True,
            "maxBulk": 50,
            "gpoTargetGroup": "QKA-Agent-Pilot-Computers",
        }
        created = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [pilot["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
        assert created.status_code == 200, created.text
        job = created.json()["jobs"][0]
        assert job["assignedQkaUserId"] is None
        token = job["machineEnrollmentToken"]
        sid = pilot["objectSid"]

    proof = deploy_svc.machine_proof_digest(token=token, computer_sid=sid, computer_guid="")
    enrolled = client.post(
        "/api/v1/agent/desktop/enroll",
        json={
            "machineEnrollmentToken": token,
            "adComputerSid": sid,
            "sidSource": "local_machine",
            "machineProof": proof,
            "computerName": "PILOT-PC",
            "deviceFingerprint": "machine-enroll-unassigned-fp",
            "domainJoinedName": "QEHC",
            "agentVersion": "0.1.0",
        },
    )
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["employeeAssigned"] is False
    assert enrolled.json()["qkaUserId"] is None
    device_id = enrolled.json()["deviceId"]
    device_key = enrolled.json()["deviceKey"]
    auth = {"X-QKA-Device-Id": device_id, "X-QKA-Device-Key": device_key}
    policy = client.get("/api/v1/agent/desktop/policy", headers=auth)
    assert policy.json()["command"] == "pause"
    assert policy.json()["employeeAssigned"] is False


def test_machine_identity_enroll_and_job_registered(client, tmp_path):
    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    pilot = next(i for i in items if i["computerName"] == "PILOT-PC")
    client.put(
        f"/api/v1/admin/workstations/{pilot['id']}/employee",
        headers=headers,
        json={"qkaUserId": str(ALLOWED_USER)},
    )

    msi = tmp_path / "QKA-Agent.msi"
    msi.write_bytes(b"MZ")
    ready = gpo_engine.GpoEngineStatus(
        available=True,
        reason="ok",
        target_group="QKA-Agent-Pilot-Computers",
        msi_path=str(msi),
        msi_present=True,
        gpmc_present=True,
    )
    settings = Settings(agent_public_base_url="https://agent.qehc.internal")
    with (
        patch.object(gpo_engine, "probe_gpo_engine", return_value=ready),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})),
        patch.object(deploy_svc, "deployment_prerequisites") as prereq_fn,
    ):
        prereq_fn.return_value = {
            **deploy_svc.deployment_prerequisites(settings),
            "deployEnabled": True,
            "httpsConfigured": True,
            "httpsEndpoint": "https://agent.qehc.internal",
            "blockers": [],
            "gpoEngineAvailable": True,
            "msiPresent": True,
            "maxBulk": 50,
            "gpoTargetGroup": "QKA-Agent-Pilot-Computers",
        }
        created = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [pilot["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
        assert created.status_code == 200, created.text
        token = created.json()["jobs"][0]["machineEnrollmentToken"]
        sid = pilot["objectSid"]

    # Spoofed SID / missing proof rejected
    spoof = client.post(
        "/api/v1/agent/desktop/enroll",
        json={
            "machineEnrollmentToken": token,
            "adComputerSid": "S-1-5-21-SPOOF",
            "sidSource": "local_machine",
            "machineProof": "deadbeef",
            "computerName": "PILOT-PC",
            "deviceFingerprint": "machine-enroll-fp-1",
            "agentVersion": "0.1.0",
        },
    )
    assert spoof.status_code in (403, 400)

    no_proof = client.post(
        "/api/v1/agent/desktop/enroll",
        json={
            "machineEnrollmentToken": token,
            "adComputerSid": sid,
            "sidSource": "local_machine",
            "computerName": "PILOT-PC",
            "deviceFingerprint": "machine-enroll-fp-1",
            "agentVersion": "0.1.0",
        },
    )
    assert no_proof.status_code == 403

    proof = deploy_svc.machine_proof_digest(token=token, computer_sid=sid, computer_guid="")
    enrolled = client.post(
        "/api/v1/agent/desktop/enroll",
        json={
            "machineEnrollmentToken": token,
            "adComputerSid": sid,
            "sidSource": "local_machine",
            "machineProof": proof,
            "computerName": "PILOT-PC",
            "deviceFingerprint": "machine-enroll-fp-1",
            "domainJoinedName": "QEHC",
            "agentVersion": "0.1.0",
        },
    )
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["enrollmentMode"] == "machine_identity"
    device_id = enrolled.json()["deviceId"]
    device_key = enrolled.json()["deviceKey"]

    # Token single-use
    reuse = client.post(
        "/api/v1/agent/desktop/enroll",
        json={
            "machineEnrollmentToken": token,
            "adComputerSid": sid,
            "sidSource": "local_machine",
            "machineProof": proof,
            "computerName": "PILOT-PC",
            "deviceFingerprint": "machine-enroll-fp-2",
            "agentVersion": "0.1.0",
        },
    )
    assert reuse.status_code == 403

    auth = {"X-QKA-Device-Id": device_id, "X-QKA-Device-Key": device_key}
    hb = client.post(
        "/api/v1/agent/desktop/heartbeat",
        headers=auth,
        json={"health": "ok", "agentVersion": "0.1.0"},
    )
    assert hb.status_code == 200

    listed = client.get("/api/v1/admin/workstations?scopeOnly=false&search=PILOT", headers=headers)
    row = next(i for i in listed.json()["items"] if i["computerName"] == "PILOT-PC")
    assert row["deploymentJob"]["status"] == "installed_agent_registered"
    assert row["desktopDeviceId"] == device_id

    # Revoke credentials
    rev = client.post(f"/api/v1/admin/desktop-devices/{device_id}/revoke", headers=headers)
    assert rev.status_code == 200
    denied_hb = client.post(
        "/api/v1/agent/desktop/heartbeat",
        headers=auth,
        json={"health": "ok"},
    )
    assert denied_hb.status_code in (401, 403)


def test_offline_workstation_still_schedules_without_claiming_install(client, tmp_path):
    """Reachability is independent of deployment job scheduling."""
    _gateway, probe = _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    pilot = next(i for i in items if i["computerName"] == "PILOT-PC")
    client.put(
        f"/api/v1/admin/workstations/{pilot['id']}/employee",
        headers=headers,
        json={"qkaUserId": str(ALLOWED_USER)},
    )
    probe.default_ping = PingResult(False, "No ICMP reply.")
    client.post(
        f"/api/v1/admin/workstations/{pilot['id']}/ping",
        headers=headers,
        json={"timeoutMs": 500},
    )

    msi = tmp_path / "QKA-Agent.msi"
    msi.write_bytes(b"MZ")
    ready = gpo_engine.GpoEngineStatus(
        available=True,
        reason="ok",
        target_group="QKA-Agent-Pilot-Computers",
        msi_path=str(msi),
        msi_present=True,
        gpmc_present=True,
    )
    settings = Settings(agent_public_base_url="https://agent.qehc.internal")
    with (
        patch.object(gpo_engine, "probe_gpo_engine", return_value=ready),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})),
        patch.object(deploy_svc, "deployment_prerequisites") as prereq_fn,
    ):
        prereq_fn.return_value = {
            **deploy_svc.deployment_prerequisites(settings),
            "deployEnabled": True,
            "httpsConfigured": True,
            "httpsEndpoint": "https://agent.qehc.internal",
            "blockers": [],
            "gpoEngineAvailable": True,
            "msiPresent": True,
            "maxBulk": 50,
            "gpoTargetGroup": "QKA-Agent-Pilot-Computers",
        }
        created = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [pilot["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
        assert created.status_code == 200
        assert created.json()["jobs"][0]["status"] == "scheduled"
        assert created.json()["realPcDeployExecuted"] is False


def test_retry_failed_job_without_duplicate(client):
    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    pilot = next(i for i in items if i["computerName"] == "PILOT-PC")
    client.put(
        f"/api/v1/admin/workstations/{pilot['id']}/employee",
        headers=headers,
        json={"qkaUserId": str(ALLOWED_USER)},
    )
    with client.app.state.session_factory() as session:
        now = datetime.now(timezone.utc)
        job = AgentDeploymentJob(
            id=uuid.uuid4(),
            workstation_id=uuid.UUID(pilot["id"]),
            assigned_qka_user_id=ALLOWED_USER,
            requested_by_user_id=ALLOWED_USER,
            approved_by_user_id=ALLOWED_USER,
            status=DEPLOY_FAILED,
            attempt_count=1,
            last_error="GPO apply failed",
            msi_version="0.1.0",
            engine="gpo",
            enrollment_mode="machine_identity",
            gpo_target_group="QKA-Agent-Pilot-Computers",
            confirmation_phrase=deploy_svc.CONFIRM_PHRASE,
            created_at=now,
            updated_at=now,
            approved_at=now,
        )
        session.add(job)
        session.commit()
        job_id = str(job.id)

    with patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})):
        retried = client.post(f"/api/v1/admin/workstations/deploy/{job_id}/retry", headers=headers)
    assert retried.status_code == 200, retried.text
    # Without live HTTPS/GPO, retry stays waiting — never invents installing.
    assert retried.json()["status"] in {"waiting_for_computer", "scheduled"}
    assert retried.json()["attemptCount"] == 2


def test_deploy_rejects_non_pilot_sid_creates_no_token(client, tmp_path):
    """Unauthorized computer cannot receive a deployment job or enrollment token."""
    from qka_shared.models import MachineEnrollmentToken
    from sqlalchemy import func, select

    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    finance = next(i for i in items if i["computerName"] == "FINANCE-01")
    assert finance["objectSid"] == FINANCE_SID

    msi = tmp_path / "QKA-Agent.msi"
    msi.write_bytes(b"MZ")
    ready = gpo_engine.GpoEngineStatus(
        available=True,
        reason="ok",
        target_group="QKA-Agent-Pilot-Computers",
        msi_path=str(msi),
        msi_present=True,
        gpmc_present=True,
    )
    settings = Settings(agent_public_base_url="https://agent.qehc.internal")
    # Only WSIT02's real SID is in the pilot group — FINANCE-01 must be rejected.
    with (
        patch.object(gpo_engine, "probe_gpo_engine", return_value=ready),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({WSIT02_SID})),
        patch.object(deploy_svc, "deployment_prerequisites") as prereq_fn,
    ):
        prereq_fn.return_value = {
            **deploy_svc.deployment_prerequisites(settings),
            "deployEnabled": True,
            "httpsConfigured": True,
            "httpsEndpoint": "https://agent.qehc.internal",
            "blockers": [],
            "gpoEngineAvailable": True,
            "msiPresent": True,
            "maxBulk": 50,
            "gpoTargetGroup": "QKA-Agent-Pilot-Computers",
            "approvedPilotComputer": "WSIT02",
        }
        before = 0
        with client.app.state.session_factory() as session:
            before = session.execute(select(func.count()).select_from(MachineEnrollmentToken)).scalar_one()

        denied = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [finance["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
        assert denied.status_code == 400
        detail = denied.json()["detail"].lower()
        assert "not authorized for pilot deployment" in detail
        assert "wsit02" in detail

        with client.app.state.session_factory() as session:
            after = session.execute(select(func.count()).select_from(MachineEnrollmentToken)).scalar_one()
            jobs = session.execute(select(func.count()).select_from(AgentDeploymentJob)).scalar_one()
        assert after == before
        assert jobs == 0


def test_list_marks_non_pilot_ineligible_for_deploy(client, tmp_path):
    _install(client)
    headers = _admin(client)
    _sync_workstations(client, headers)
    settings = Settings(agent_public_base_url="https://agent.qehc.internal")
    (tmp_path / "QKA-Agent.msi").write_bytes(b"MZ")
    ready = gpo_engine.GpoEngineStatus(
        available=True,
        reason="ok",
        target_group="QKA-Agent-Pilot-Computers",
        msi_path=str(tmp_path / "QKA-Agent.msi"),
        msi_present=True,
        gpmc_present=True,
    )
    # Build prereq from the real function before patching (mock would recurse).
    with patch.object(gpo_engine, "probe_gpo_engine", return_value=ready):
        base_prereq = deploy_svc.deployment_prerequisites(settings)
    base_prereq = {
        **base_prereq,
        "deployEnabled": True,
        "httpsConfigured": True,
        "httpsEndpoint": "https://agent.qehc.internal",
        "tlsTrust": "server_ok_pilot_cer_required",
        "blockers": [],
        "gpoEngineAvailable": True,
        "msiPresent": True,
        "maxBulk": 50,
        "gpoTargetGroup": "QKA-Agent-Pilot-Computers",
        "machineIdentityEnrollmentReady": True,
    }
    with (
        patch.object(gpo_engine, "probe_gpo_engine", return_value=ready),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})),
        patch.object(deploy_svc, "deployment_prerequisites", return_value=base_prereq),
    ):
        listed = client.get("/api/v1/admin/workstations?scopeOnly=false", headers=headers)
        assert listed.status_code == 200
        by_name = {i["computerName"]: i for i in listed.json()["items"]}
        assert by_name["PILOT-PC"]["deployAgent"]["pilotGroupMember"] is True
        assert by_name["PILOT-PC"]["deployAgent"]["enabled"] is True
        assert by_name["FINANCE-01"]["deployAgent"]["pilotGroupMember"] is False
        assert by_name["FINANCE-01"]["deployAgent"]["enabled"] is False
        assert "QKA-Agent-Pilot-Computers" in by_name["FINANCE-01"]["deployAgent"]["note"]


def _ready_prereq(settings: Settings) -> dict:
    base = deploy_svc.deployment_prerequisites(settings)
    return {
        **base,
        "deployEnabled": True,
        "httpsConfigured": True,
        "httpsEndpoint": "https://agent.qehc.internal",
        "blockers": [],
        "gpoEngineAvailable": True,
        "msiPresent": True,
        "maxBulk": 50,
        "gpoTargetGroup": "QKA-Agent-Pilot-Computers",
    }


def test_install_authorization_rejects_absent_expired_cancelled_and_foreign_sid(client, tmp_path):
    """Token file presence is not proof — the API must see a live uncancelled token."""
    from qka_shared.models import MachineEnrollmentToken

    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    pilot = next(i for i in items if i["computerName"] == "PILOT-PC")
    msi = tmp_path / "QKA-Agent.msi"
    msi.write_bytes(b"MZ-fake")
    ready = gpo_engine.GpoEngineStatus(
        available=True,
        reason="ok",
        target_group="QKA-Agent-Pilot-Computers",
        msi_path=str(msi),
        msi_present=True,
        gpmc_present=True,
    )
    settings = Settings(agent_public_base_url="https://agent.qehc.internal")
    absent = client.post(
        "/api/v1/agent/desktop/install-authorization",
        json={"token": "", "adComputerSid": PILOT_SID},
    )
    assert absent.status_code == 200
    assert absent.json()["authorized"] is False
    assert absent.json()["reason"] == "absent"

    with (
        patch.object(gpo_engine, "probe_gpo_engine", return_value=ready),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})),
        patch.object(deploy_svc, "deployment_prerequisites", return_value=_ready_prereq(settings)),
    ):
        created = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [pilot["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
    assert created.status_code == 200, created.text
    token = created.json()["jobs"][0]["machineEnrollmentToken"]
    job_id = created.json()["jobs"][0]["id"]
    assert token

    ok = client.post(
        "/api/v1/agent/desktop/install-authorization",
        json={"token": token, "adComputerSid": PILOT_SID},
    )
    assert ok.status_code == 200
    assert ok.json()["authorized"] is True
    assert ok.json()["reason"] == "ok"
    assert "token" not in ok.json()

    foreign = client.post(
        "/api/v1/agent/desktop/install-authorization",
        json={"token": token, "adComputerSid": FINANCE_SID},
    )
    assert foreign.json()["authorized"] is False
    assert foreign.json()["reason"] == "foreign_sid"

    with client.app.state.session_factory() as session:
        row = session.execute(
            select(MachineEnrollmentToken).where(MachineEnrollmentToken.job_id == uuid.UUID(job_id))
        ).scalar_one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.commit()
    expired = client.post(
        "/api/v1/agent/desktop/install-authorization",
        json={"token": token, "adComputerSid": PILOT_SID},
    )
    assert expired.json()["authorized"] is False
    assert expired.json()["reason"] == "expired"

    with client.app.state.session_factory() as session:
        row = session.execute(
            select(MachineEnrollmentToken).where(MachineEnrollmentToken.job_id == uuid.UUID(job_id))
        ).scalar_one()
        row.expires_at = datetime.now(timezone.utc) + timedelta(hours=2)
        session.commit()
    cancelled = client.post(
        f"/api/v1/admin/workstations/deploy/{job_id}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200
    after = client.post(
        "/api/v1/agent/desktop/install-authorization",
        json={"token": token, "adComputerSid": PILOT_SID},
    )
    assert after.json()["authorized"] is False
    assert after.json()["reason"] == "cancelled"


def test_scheduled_task_template_is_system_interval_not_startup():
    root = Path(__file__).resolve().parents[2] / "scripts" / "gpo-agent-deploy"
    task = (root / "ScheduledTasks.template.xml").read_text(encoding="utf-8")
    script = (root / "Install-QkaAgentFromUnc.template.ps1").read_text(encoding="utf-8")
    assert "PT2M" in task
    assert "S-1-5-18" in task
    assert "IgnoreNew" in task
    assert "install-authorization" in script
    assert "install-progress" in script
    assert "Get-MachineProof" in script
    assert "QkaDesktopAgent" in script
    assert "norestart" in script
    assert "3010" in script


def _proof(token: str, sid: str) -> str:
    return deploy_svc.machine_proof_digest(token=token, computer_sid=sid, computer_guid="")


def test_install_progress_pending_to_in_progress_to_failed_and_rejects_forged(client, tmp_path):
    """Approved → In Progress (UI); started → installing; MSI fail → Failed; forged proof rejected."""
    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    pilot = next(i for i in items if i["computerName"] == "PILOT-PC")
    msi = tmp_path / "QKA-Agent.msi"
    msi.write_bytes(b"MZ-fake")
    ready = gpo_engine.GpoEngineStatus(
        available=True,
        reason="ok",
        target_group="QKA-Agent-Pilot-Computers",
        msi_path=str(msi),
        msi_present=True,
        gpmc_present=True,
    )
    settings = Settings(agent_public_base_url="https://agent.qehc.internal")
    with (
        patch.object(gpo_engine, "probe_gpo_engine", return_value=ready),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})),
        patch.object(deploy_svc, "deployment_prerequisites", return_value=_ready_prereq(settings)),
    ):
        created = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [pilot["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
    assert created.status_code == 200, created.text
    job = created.json()["jobs"][0]
    token = job["machineEnrollmentToken"]
    job_id = job["id"]
    assert job["status"] == "scheduled"
    assert job["displayStatus"] == "in_progress"
    assert job.get("installStartedAt") in (None, "")

    # Deploy / redeploy must show In Progress immediately (not Pending).
    listed = client.get("/api/v1/admin/workstations?scopeOnly=false", headers=headers)
    row = next(i for i in listed.json()["items"] if i["id"] == pilot["id"])
    assert row["deploymentJob"]["displayStatus"] == "in_progress"

    forged = client.post(
        "/api/v1/agent/desktop/install-progress",
        json={
            "token": token,
            "adComputerSid": PILOT_SID,
            "machineProof": "0" * 64,
            "phase": "started",
        },
    )
    assert forged.status_code == 403

    started = client.post(
        "/api/v1/agent/desktop/install-progress",
        json={
            "token": token,
            "adComputerSid": PILOT_SID,
            "machineProof": _proof(token, PILOT_SID),
            "phase": "started",
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["displayStatus"] == "in_progress"
    assert started.json()["status"] == "installing"

    # Cannot cancel after install started.
    cancel = client.post(f"/api/v1/admin/workstations/deploy/{job_id}/cancel", headers=headers)
    assert cancel.status_code == 400

    failed = client.post(
        "/api/v1/agent/desktop/install-progress",
        json={
            "token": token,
            "adComputerSid": PILOT_SID,
            "machineProof": _proof(token, PILOT_SID),
            "phase": "failed",
            "msiExitCode": 1603,
            "detail": "msiexec_exit=1603",
        },
    )
    assert failed.status_code == 200
    assert failed.json()["displayStatus"] == "failed"
    assert failed.json()["msiExitCode"] == 1603


def test_install_progress_msi_success_stays_in_progress_until_registration(client, tmp_path):
    """MSI 0 / 3010 → still In Progress (waiting registration), never Online by itself."""
    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    pilot = next(i for i in items if i["computerName"] == "PILOT-PC")
    msi = tmp_path / "QKA-Agent.msi"
    msi.write_bytes(b"MZ-fake")
    ready = gpo_engine.GpoEngineStatus(
        available=True,
        reason="ok",
        target_group="QKA-Agent-Pilot-Computers",
        msi_path=str(msi),
        msi_present=True,
        gpmc_present=True,
    )
    settings = Settings(agent_public_base_url="https://agent.qehc.internal")
    with (
        patch.object(gpo_engine, "probe_gpo_engine", return_value=ready),
        patch.object(gpo_engine, "list_pilot_group_member_sids", return_value=frozenset({PILOT_SID})),
        patch.object(deploy_svc, "deployment_prerequisites", return_value=_ready_prereq(settings)),
    ):
        created = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [pilot["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
    token = created.json()["jobs"][0]["machineEnrollmentToken"]
    proof = _proof(token, PILOT_SID)
    assert (
        client.post(
            "/api/v1/agent/desktop/install-progress",
            json={"token": token, "adComputerSid": PILOT_SID, "machineProof": proof, "phase": "started"},
        ).status_code
        == 200
    )
    done = client.post(
        "/api/v1/agent/desktop/install-progress",
        json={
            "token": token,
            "adComputerSid": PILOT_SID,
            "machineProof": proof,
            "phase": "msi_finished",
            "msiExitCode": 3010,
            "detail": "msiexec_exit=3010_restart_required",
        },
    )
    assert done.status_code == 200
    body = done.json()
    assert body["displayStatus"] == "in_progress"
    assert body["restartRequired"] is True
    assert body["msiExitCode"] == 3010
    assert body["status"] == "installing"


def test_wsit02_sid_recognized_as_only_approved_pilot_identity():
    """Document/verify the live WSIT02 objectSid used for pilot authorization."""
    assert WSIT02_SID.startswith("S-1-5-21-")
    assert gpo_engine.computer_sid_in_pilot_group(
        WSIT02_SID, member_sids=frozenset({WSIT02_SID})
    )
    assert not gpo_engine.computer_sid_in_pilot_group(
        FINANCE_SID, member_sids=frozenset({WSIT02_SID})
    )
    assert not gpo_engine.computer_sid_in_pilot_group(
        "WSIT02", member_sids=frozenset({WSIT02_SID})
    )  # name-only must never authorize


def test_compute_display_status_lifecycle_transitions():
    from types import SimpleNamespace

    from qka_shared.models import (
        DEPLOY_CANCELLED,
        DEPLOY_FAILED,
        DEPLOY_INSTALLING,
        DEPLOY_INSTALLED_REGISTERED,
        DEPLOY_SCHEDULED,
        DEPLOY_TIMED_OUT,
    )

    def job(status: int) -> SimpleNamespace:
        return SimpleNamespace(status=status)

    assert deploy_svc.compute_display_status(job=job(DEPLOY_SCHEDULED)) == "in_progress"
    assert deploy_svc.compute_display_status(job=job(DEPLOY_INSTALLING)) == "in_progress"
    assert (
        deploy_svc.compute_display_status(
            job=job(DEPLOY_INSTALLED_REGISTERED),
            agent_online=False,
            agent_status="not_registered",
            has_heartbeat=False,
        )
        == "installed"
    )
    assert (
        deploy_svc.compute_display_status(
            job=job(DEPLOY_INSTALLED_REGISTERED),
            agent_online=True,
            agent_status="connected",
            has_heartbeat=True,
        )
        == "deployed"
    )
    assert deploy_svc.compute_display_status(job=job(DEPLOY_FAILED)) == "failed"
    assert deploy_svc.compute_display_status(job=job(DEPLOY_TIMED_OUT)) == "timed_out"
    assert deploy_svc.compute_display_status(job=job(DEPLOY_CANCELLED)) == "cancelled"
    # Registration after timeout still surfaces as deployed when heartbeat exists.
    assert (
        deploy_svc.compute_display_status(
            job=job(DEPLOY_TIMED_OUT),
            agent_online=True,
            agent_status="connected",
            has_heartbeat=True,
        )
        == "deployed"
    )


def test_reconcile_stale_pending_job_times_out(client):
    _install(client)
    headers = _admin(client)
    items = _sync_workstations(client, headers)
    pilot = next(i for i in items if i["computerName"] == "PILOT-PC")
    now = datetime.now(timezone.utc)
    from qka_api.db import create_session_factory
    from qka_shared.config import get_settings
    from qka_shared.models import DEPLOY_SCHEDULED, DEPLOY_TIMED_OUT, AgentDeploymentJob, MachineEnrollmentToken

    settings = get_settings()
    _engine, factory = create_session_factory(settings)
    job_id = uuid.uuid4()
    tok_id = uuid.uuid4()
    with factory() as session:
        session.add(
            AgentDeploymentJob(
                id=job_id,
                workstation_id=uuid.UUID(pilot["id"]),
                requested_by_user_id=ALLOWED_USER,
                status=DEPLOY_SCHEDULED,
                attempt_count=1,
                engine="gpo",
                created_at=now - timedelta(hours=2),
                updated_at=now - timedelta(hours=2),
                scheduled_at=now - timedelta(hours=2),
            )
        )
        session.add(
            MachineEnrollmentToken(
                id=tok_id,
                job_id=job_id,
                workstation_id=uuid.UUID(pilot["id"]),
                ad_computer_sid=PILOT_SID,
                token_hash="a" * 64,
                proof_hash="",
                expires_at=now + timedelta(hours=12),
                created_at=now - timedelta(hours=2),
            )
        )
        session.commit()
        # 2h is still inside the default GPO-aware pending window (180m) → must NOT time out.
        settings_gpo = Settings(
            agent_gpo_task_distribution="ou_computers",
            agent_deploy_pending_timeout_minutes=180,
            agent_deploy_gpo_provision_timeout_minutes=180,
            agent_deploy_installing_timeout_minutes=120,
        )
        assert deploy_svc.reconcile_stale_deployment_jobs(session, settings_gpo) == 0
        session.refresh(session.get(AgentDeploymentJob, job_id))
        assert session.get(AgentDeploymentJob, job_id).status == DEPLOY_SCHEDULED
        assert session.get(MachineEnrollmentToken, tok_id).consumed_at is None

        # Explicit short window still times out and revokes the token.
        settings_fast = Settings(
            agent_gpo_task_distribution="ou_computers",
            agent_deploy_pending_timeout_minutes=30,
            agent_deploy_gpo_provision_timeout_minutes=30,
            agent_deploy_installing_timeout_minutes=120,
        )
        changed = deploy_svc.reconcile_stale_deployment_jobs(session, settings_fast)
        assert changed == 1
        session.commit()
        row = session.get(AgentDeploymentJob, job_id)
        assert row is not None
        assert row.status == DEPLOY_TIMED_OUT
        assert row.last_result == "timed_out_gpo_wait"
        assert "Timed out" in (row.last_error or "")
        assert "revoked" in (row.last_error or "").lower()
        tok = session.get(MachineEnrollmentToken, tok_id)
        assert tok is not None and tok.consumed_at is not None

    listed = client.get("/api/v1/admin/workstations?scopeOnly=false", headers=headers)
    assert listed.status_code == 200
    row = next(i for i in listed.json()["items"] if i["id"] == pilot["id"])
    assert row["deploymentJob"]["displayStatus"] == "timed_out"
    assert row["deploymentJob"]["status"] == "timed_out"


def test_pending_wait_covers_background_gpo_refresh():
    minutes, phase = deploy_svc._pending_wait_minutes(
        Settings(
            agent_gpo_task_distribution="ou_computers",
            agent_deploy_pending_timeout_minutes=180,
            agent_deploy_gpo_provision_timeout_minutes=180,
        )
    )
    assert minutes >= 180
    assert phase.startswith("gpo")
    # Installing window stays separate/shorter than a multi-hour GPO wait default.
    assert int(Settings().agent_deploy_installing_timeout_minutes) <= minutes
