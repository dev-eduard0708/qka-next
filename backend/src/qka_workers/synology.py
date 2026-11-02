from __future__ import annotations

import logging
import threading
from sqlalchemy.orm import Session, sessionmaker

from qka_api.services.synology import process_next_job
from qka_api.smb import SmbGateway, default_gateway
from qka_shared.config import Settings

log = logging.getLogger("qka.worker.synology")


def tick(factory: sessionmaker[Session], settings: Settings, gateway: SmbGateway | None = None) -> None:
    smb = gateway or default_gateway()
    with factory() as session:
        try:
            process_next_job(session, settings, smb)
            session.commit()
        except Exception:
            session.rollback()
            log.exception("synology.worker.tick_failed")


def start_embedded_worker(
    factory: sessionmaker[Session],
    settings: Settings,
    gateway: SmbGateway | None = None,
    stop_event: threading.Event | None = None,
    interval_seconds: float = 2.0,
) -> tuple[threading.Event, threading.Thread]:
    stop = stop_event or threading.Event()

    def loop() -> None:
        log.info("synology.worker.started interval=%.1fs", interval_seconds)
        while not stop.wait(interval_seconds):
            tick(factory, settings, gateway)
        log.info("synology.worker.stopped")

    thread = threading.Thread(target=loop, name="qka-synology-worker", daemon=True)
    thread.start()
    return stop, thread


def should_embed(settings: Settings) -> bool:
    import os

    if not settings.worker_embedded:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return True
