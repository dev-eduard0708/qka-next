from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

from qka_shared.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "qka_api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        loop="asyncio",
    )


if __name__ == "__main__":
    main()
