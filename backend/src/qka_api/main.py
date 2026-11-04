from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from qka_api.db import create_session_factory
from qka_api.google_oidc import GoogleOidcService
from qka_api.local_login import bootstrap_local_super_admin
from qka_api.mcp_oauth import McpOAuthService
from qka_api.mcp_server import create_mcp_server
from qka_api.routes.access import router as access_router
from qka_api.routes.ai_chat import router as ai_chat_router
from qka_api.routes.auth import compat_router as auth_compat_router
from qka_api.routes.auth import router as auth_router
from qka_api.routes.documents import router as documents_router
from qka_api.routes.health import router as health_router
from qka_api.routes.active_directory import router as active_directory_router
from qka_api.routes.integrations import router as integrations_router
from qka_api.routes.desktop_agent import admin_router as desktop_admin_router
from qka_api.routes.desktop_agent import agent_router as desktop_agent_router
from qka_api.routes.mcp_oauth import router as mcp_oauth_router
from qka_api.routes.synology import router as synology_router
from qka_api.routes.workstations import router as workstations_router
from mcp.server.transport_security import TransportSecuritySettings

from qka_api.ldap_client import default_ldap_gateway
from qka_api.services.network_probe import default_network_probe
from qka_api.smb import default_gateway
from qka_shared.config import get_settings
from qka_workers.synology import should_embed, start_embedded_worker

# Filled during FastAPI startup; consumed by MCP tool lifespan.
_MCP_CONTEXT: dict = {}


class _RedactOAuthAccessLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "google/callback" in message or "code=" in message:
            record.msg = "google callback [query redacted]"
            record.args = ()
        return True


def create_app() -> FastAPI:
    settings = get_settings()
    mcp_oauth = McpOAuthService(settings)

    @asynccontextmanager
    async def mcp_tool_lifespan(_server):
        yield _MCP_CONTEXT

    mcp = create_mcp_server(mcp_oauth, settings, mcp_tool_lifespan)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=settings.log_level)
        logging.getLogger("uvicorn.access").addFilter(_RedactOAuthAccessLog())
        engine, factory = create_session_factory(settings)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = factory
        app.state.google_oidc = GoogleOidcService(settings)
        app.state.smb_gateway = default_gateway()
        app.state.ldap_gateway = default_ldap_gateway()
        app.state.network_probe = default_network_probe()
        app.state.mcp_oauth = mcp_oauth
        app.state.mcp = mcp
        _MCP_CONTEXT.clear()
        _MCP_CONTEXT["session_factory"] = factory
        _MCP_CONTEXT["settings"] = settings
        with factory() as session:
            bootstrap_local_super_admin(session, settings)
            session.commit()
        worker_stop = None
        if should_embed(settings):
            worker_stop, _ = start_embedded_worker(factory, settings, app.state.smb_gateway)
            app.state.worker_stop = worker_stop
        async with mcp.session_manager.run():
            yield
        if worker_stop is not None:
            worker_stop.set()
        engine.dispose()

    app = FastAPI(
        title="QKA-Next API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"],
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(auth_compat_router)
    app.include_router(access_router)
    app.include_router(documents_router)
    app.include_router(ai_chat_router)
    app.include_router(synology_router)
    app.include_router(active_directory_router)
    app.include_router(integrations_router)
    app.include_router(desktop_agent_router)
    app.include_router(desktop_admin_router)
    app.include_router(workstations_router)
    app.include_router(mcp_oauth_router)
    # Must call streamable_http_app before lifespan uses session_manager.
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1",
            "127.0.0.1:*",
            "localhost",
            "localhost:*",
            "testserver",
            "testserver:*",
        ],
        allowed_origins=[
            "http://127.0.0.1:5186",
            "http://localhost:5186",
            "http://127.0.0.1:5265",
            "http://localhost:5265",
        ],
    )
    app.mount(
        "/mcp",
        mcp.streamable_http_app(
            streamable_http_path="/",
            json_response=True,
            stateless_http=True,
            transport_security=transport_security,
        ),
    )
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "qka_api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
