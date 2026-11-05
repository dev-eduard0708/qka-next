"""Agent remote deployment: prerequisites, GPO scope, machine-identity enroll, jobs."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from qka_api.services import agent_deployment as deploy_svc
from qka_api.services import gpo_engine
from qka_api.services.network_probe import PingResult
from qka_shared.config import Settings
from qka_shared.models import DEPLOY_FAILED, AgentDeploymentJob
from tests.conftest import ALLOWED_USER, auth_cookies
from tests.test_workstations import _admin, _ensure_ad_connection, _install


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
        }
        # Wrong confirmation
        bad = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={"workstationIds": [pilot["id"]], "confirmation": "YES", "approve": True},
        )
        assert bad.status_code == 400

        # Single selected PC
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

        # Bulk remaining computer
        bulk = client.post(
            "/api/v1/admin/workstations/deploy",
            headers=headers,
            json={
                "workstationIds": [finance["id"]],
                "confirmation": deploy_svc.CONFIRM_PHRASE,
                "approve": True,
            },
        )
        assert bulk.status_code == 200, bulk.text
        assert bulk.json()["gpoManifest"]["computers"] == ["FINANCE-01"]


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

    retried = client.post(f"/api/v1/admin/workstations/deploy/{job_id}/retry", headers=headers)
    assert retried.status_code == 200, retried.text
    # Without live HTTPS/GPO, retry stays waiting — never invents installing.
    assert retried.json()["status"] in {"waiting_for_computer", "scheduled"}
    assert retried.json()["attemptCount"] == 2
