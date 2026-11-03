from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from pydantic import AliasChoices, BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _empty_str_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


OptionalUuid = Annotated[UUID | None, BeforeValidator(_empty_str_to_none)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QKA_",
        env_file=(".env", "../.env", "../../.env"),
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://qka_test@127.0.0.1:55432/qka_next_dev"
    database_read_only: bool = True
    api_host: str = "127.0.0.1"
    api_port: int = 5265
    cors_origins: str = "http://127.0.0.1:5186"
    session_secret: str = "dev-only-change-me"
    session_hours: int = 8
    environment: str = "development"
    cookie_secure: bool = False
    frontend_origin: str = "http://127.0.0.1:5186"
    dev_session_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:5186/api/v1/auth/google/callback"
    google_hosted_domain: str = "qehc.edu.sa"
    google_authority: str = "https://accounts.google.com"
    local_admin_username: str = "super"
    local_admin_password: str = ""
    legacy_pilot_user_id: OptionalUuid = None
    acl_allow_principal_contains: str = ""
    pilot_folder_prefix: str = "EDUARDTESTCLOUD"
    approved_share_id: OptionalUuid = None
    max_search_results: int = 20
    max_preview_chars: int = 4000
    preview_cache_dir: str = ""
    preview_max_source_bytes: int = 25_000_000
    preview_max_pages: int = 100
    libreoffice_path: str = ""
    libreoffice_timeout_seconds: int = 120
    log_level: str = "INFO"
    credential_key: str = ""
    worker_embedded: bool = True
    smb_allowed_hosts: str = ""
    # MCP / ChatGPT Business (QKA-Next staging only; never overwrite legacy :5255)
    mcp_oauth_enabled: bool = False
    mcp_public_base_url: str = "http://127.0.0.1:5265"
    mcp_resource_path: str = "/mcp"
    mcp_documents_scope: str = "mcp:documents.read"
    mcp_google_redirect_path: str = "/oauth/google/callback"
    mcp_inspector_bearer: str = ""
    mcp_inspector_user_id: OptionalUuid = None
    mcp_additional_allowed_resources: str = ""
    mcp_tunnel_ready_url: str = "http://127.0.0.1:8081/readyz"
    mcp_chatgpt_plugin_name: str = "QKA-Next DEV – QEC Knowledge AI"
    mcp_chatgpt_environment: str = "staging"
    mcp_chatgpt_live_verified_at: str = ""
    # Native OpenAI chat (server-side only). Prefer OPENAI_API_KEY; QKA_OPENAI_API_KEY also accepted.
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("QKA_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    openai_model: str = "gpt-4.1-mini"
    openai_max_output_tokens: int = 2048
    openai_max_context_chars: int = 48000
    openai_max_history_messages: int = 24
    openai_upload_max_bytes: int = 5_000_000
    openai_data_sharing_permitted: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def openai_configured(self) -> bool:
        return bool((self.openai_api_key or "").strip())

    @property
    def mcp_issuer(self) -> str:
        return (self.mcp_public_base_url or "").rstrip("/")

    @property
    def mcp_resource(self) -> str:
        base = self.mcp_issuer
        path = self.mcp_resource_path if self.mcp_resource_path.startswith("/") else f"/{self.mcp_resource_path}"
        return f"{base}{path}"

    @property
    def mcp_additional_resources_list(self) -> list[str]:
        return [item.strip() for item in self.mcp_additional_allowed_resources.split(",") if item.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    def dev_session_allowed(self) -> bool:
        if self.is_production:
            return False
        return self.dev_session_enabled


@lru_cache
def get_settings() -> Settings:
    return Settings()
