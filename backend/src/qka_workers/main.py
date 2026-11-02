from __future__ import annotations

import logging
import time

from qka_api.db import create_session_factory
from qka_api.smb import default_gateway
from qka_shared.config import get_settings
from qka_workers.jobs import REGISTERED_JOBS
from qka_workers.synology import tick


def run() -> None:
    logging.basicConfig(level="INFO")
    settings = get_settings()
    _, factory = create_session_factory(settings)
    gateway = default_gateway()
    logging.info("QKA-Next worker started. Registered jobs=%s.", ",".join(REGISTERED_JOBS))
    while True:
        tick(factory, settings, gateway)
        time.sleep(2)


if __name__ == "__main__":
    run()
