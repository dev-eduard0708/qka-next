"""Live AD workstation sync + sample DNS/ICMP probes (read-only; no PC changes)."""

from __future__ import annotations

import json
import sys
import uuid

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from qka_api.ldap_client import default_ldap_gateway
from qka_api.services import workstations as ws_svc
from qka_api.services.network_probe import default_network_probe
from qka_shared.config import get_settings
from qka_shared.models import AdConnection, AdOuScope, AdWorkstation


def main() -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    gateway = default_ldap_gateway()
    probe = default_network_probe()

    with factory() as session:
        conn = session.execute(
            select(AdConnection)
            .where(AdConnection.connection_status == 1)
            .order_by(AdConnection.updated_at.desc())
        ).scalars().first()
        if conn is None:
            print(json.dumps({"ok": False, "error": "No connected AD connection"}))
            return 2

        scopes = session.execute(
            select(AdOuScope).where(AdOuScope.connection_id == conn.id, AdOuScope.selected.is_(True))
        ).scalars().all()
        print(
            json.dumps(
                {
                    "connectionId": str(conn.id),
                    "connectionName": conn.connection_name,
                    "domain": conn.domain_dns_name,
                    "baseDn": conn.base_dn,
                    "selectedOuCount": len(scopes),
                    "selectedOus": [s.distinguished_name for s in scopes[:20]],
                },
                indent=2,
            )
        )

        # Preserve AD users — only sync computers.
        result = ws_svc.sync_workstations(
            session,
            settings,
            gateway,
            connection_id=conn.id,
            include_servers=False,
        )
        session.commit()
        print("SYNC", json.dumps(result, indent=2))

        present = session.execute(
            select(AdWorkstation)
            .where(AdWorkstation.is_present.is_(True), AdWorkstation.is_server_or_dc.is_(False))
            .order_by(AdWorkstation.computer_name)
        ).scalars().all()
        print("LIVE_AD_WORKSTATION_COUNT", len(present))
        sample = present[:5]
        print(
            "SAMPLE",
            json.dumps(
                [
                    {
                        "name": w.computer_name,
                        "dns": w.dns_hostname,
                        "ou": w.ou_path,
                        "enabled": w.account_enabled,
                    }
                    for w in sample
                ],
                indent=2,
            ),
        )

        # Single ping on first sample (if any)
        ping_results = []
        if sample:
            one = ws_svc.probe_workstation(session, sample[0].id, probe, timeout_ms=2000)
            session.commit()
            ping_results.append({"mode": "single", **{k: one[k] for k in (
                "computerName", "dnsHostname", "ipAddress", "reachabilityStatus", "reachabilityDetail", "lastCheckedAt"
            )}})

        # Bulk ping on up to 5 selected authorized workstations
        ids = [w.id for w in sample[:5]]
        if ids:
            bulk = ws_svc.probe_workstations_bulk(
                session, ids, probe, timeout_ms=2000, concurrency=4
            )
            session.commit()
            ping_results.append(
                {
                    "mode": "bulk",
                    "processed": bulk["processed"],
                    "results": [
                        {
                            "computerName": r["computerName"],
                            "ipAddress": r["ipAddress"],
                            "reachabilityStatus": r["reachabilityStatus"],
                        }
                        for r in bulk["results"]
                    ],
                    "note": bulk["note"],
                }
            )

        # Sanity: AD directory users unchanged count
        user_count = session.execute(text("SELECT COUNT(*) FROM qka_next_ad_directory_user")).scalar()
        print("AD_DIRECTORY_USER_COUNT", int(user_count or 0))
        print("PING", json.dumps(ping_results, indent=2))
        print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
