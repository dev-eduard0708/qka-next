"""Active Directory connection, directory cache, sync jobs, and audit.

Revision ID: 20261103_ad_integration
Revises: 20261103_mcp_capabilities
Create Date: 2026-11-03
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261103_ad_integration"
down_revision = "20261103_mcp_capabilities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_ad_connection (
                id UUID PRIMARY KEY,
                connection_name VARCHAR(256) NOT NULL DEFAULT '',
                domain_dns_name VARCHAR(256) NOT NULL DEFAULT '',
                domain_controller VARCHAR(256) NOT NULL DEFAULT '',
                protocol VARCHAR(16) NOT NULL DEFAULT 'ldaps',
                port INTEGER NOT NULL DEFAULT 636,
                base_dn VARCHAR(1024) NOT NULL DEFAULT '',
                bind_username VARCHAR(512) NOT NULL DEFAULT '',
                bind_secret_protected BYTEA NULL,
                timeout_seconds INTEGER NOT NULL DEFAULT 15,
                verify_tls BOOLEAN NOT NULL DEFAULT TRUE,
                connection_status INTEGER NOT NULL DEFAULT 0,
                last_successful_test_at TIMESTAMPTZ NULL,
                last_safe_error VARCHAR(1024) NULL,
                last_diagnostics_json TEXT NOT NULL DEFAULT '[]',
                last_successful_sync_at TIMESTAMPTZ NULL,
                last_sync_result VARCHAR(64) NULL,
                current_sync_job_id UUID NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                created_by_user_id UUID NULL,
                updated_by_user_id UUID NULL
            )
            """
        )
    )
    bind.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_ad_ou_scope (
                id UUID PRIMARY KEY,
                connection_id UUID NOT NULL,
                distinguished_name VARCHAR(2048) NOT NULL,
                name VARCHAR(512) NOT NULL DEFAULT '',
                selected BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    bind.execute(text("CREATE INDEX IF NOT EXISTS ix_qka_next_ad_ou_scope_conn ON qka_next_ad_ou_scope (connection_id)"))
    bind.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_ad_directory_user (
                id UUID PRIMARY KEY,
                connection_id UUID NOT NULL,
                object_guid VARCHAR(64) NOT NULL,
                object_sid VARCHAR(256) NOT NULL DEFAULT '',
                sam_account_name VARCHAR(256) NOT NULL DEFAULT '',
                user_principal_name VARCHAR(512) NOT NULL DEFAULT '',
                display_name VARCHAR(512) NOT NULL DEFAULT '',
                mail VARCHAR(512) NOT NULL DEFAULT '',
                department VARCHAR(512) NOT NULL DEFAULT '',
                distinguished_name VARCHAR(2048) NOT NULL DEFAULT '',
                account_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                is_present BOOLEAN NOT NULL DEFAULT TRUE,
                mapping_status INTEGER NOT NULL DEFAULT 0,
                linked_qka_user_id UUID NULL,
                linked_external_identity_id UUID NULL,
                candidate_qka_user_id UUID NULL,
                candidate_google_email VARCHAR(512) NOT NULL DEFAULT '',
                content_hash VARCHAR(64) NOT NULL DEFAULT '',
                last_seen_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    bind.execute(text("CREATE INDEX IF NOT EXISTS ix_qka_next_ad_user_conn ON qka_next_ad_directory_user (connection_id)"))
    bind.execute(text("CREATE INDEX IF NOT EXISTS ix_qka_next_ad_user_guid ON qka_next_ad_directory_user (object_guid)"))
    bind.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_ad_directory_group (
                id UUID PRIMARY KEY,
                connection_id UUID NOT NULL,
                object_guid VARCHAR(64) NOT NULL,
                object_sid VARCHAR(256) NOT NULL DEFAULT '',
                name VARCHAR(512) NOT NULL DEFAULT '',
                distinguished_name VARCHAR(2048) NOT NULL DEFAULT '',
                is_security_group BOOLEAN NOT NULL DEFAULT TRUE,
                is_present BOOLEAN NOT NULL DEFAULT TRUE,
                member_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    bind.execute(text("CREATE INDEX IF NOT EXISTS ix_qka_next_ad_group_conn ON qka_next_ad_directory_group (connection_id)"))
    bind.execute(text("CREATE INDEX IF NOT EXISTS ix_qka_next_ad_group_guid ON qka_next_ad_directory_group (object_guid)"))
    bind.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_ad_group_member (
                id UUID PRIMARY KEY,
                connection_id UUID NOT NULL,
                group_object_guid VARCHAR(64) NOT NULL,
                member_object_guid VARCHAR(64) NOT NULL DEFAULT '',
                member_object_sid VARCHAR(256) NOT NULL DEFAULT '',
                member_kind VARCHAR(16) NOT NULL DEFAULT 'user',
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    bind.execute(text("CREATE INDEX IF NOT EXISTS ix_qka_next_ad_gmem_conn ON qka_next_ad_group_member (connection_id)"))
    bind.execute(text("CREATE INDEX IF NOT EXISTS ix_qka_next_ad_gmem_group ON qka_next_ad_group_member (group_object_guid)"))
    bind.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_ad_sync_job (
                id UUID PRIMARY KEY,
                connection_id UUID NOT NULL,
                trigger VARCHAR(32) NOT NULL DEFAULT 'manual',
                status INTEGER NOT NULL DEFAULT 0,
                actor_user_id UUID NULL,
                users_discovered INTEGER NOT NULL DEFAULT 0,
                users_processed INTEGER NOT NULL DEFAULT 0,
                users_unchanged INTEGER NOT NULL DEFAULT 0,
                users_updated INTEGER NOT NULL DEFAULT 0,
                users_added INTEGER NOT NULL DEFAULT 0,
                groups_discovered INTEGER NOT NULL DEFAULT 0,
                linked_count INTEGER NOT NULL DEFAULT 0,
                pending_count INTEGER NOT NULL DEFAULT 0,
                conflict_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                enumeration_complete BOOLEAN NOT NULL DEFAULT FALSE,
                cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
                safe_error VARCHAR(1024) NULL,
                created_at TIMESTAMPTZ NOT NULL,
                started_at TIMESTAMPTZ NULL,
                completed_at TIMESTAMPTZ NULL
            )
            """
        )
    )
    bind.execute(text("CREATE INDEX IF NOT EXISTS ix_qka_next_ad_sync_conn ON qka_next_ad_sync_job (connection_id)"))
    bind.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS qka_next_ad_audit (
                id UUID PRIMARY KEY,
                connection_id UUID NULL,
                action VARCHAR(64) NOT NULL,
                actor_user_id UUID NULL,
                detail VARCHAR(2000) NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    bind.execute(text("CREATE INDEX IF NOT EXISTS ix_qka_next_ad_audit_conn ON qka_next_ad_audit (connection_id)"))


def downgrade() -> None:
    bind = op.get_bind()
    for table in (
        "qka_next_ad_audit",
        "qka_next_ad_sync_job",
        "qka_next_ad_group_member",
        "qka_next_ad_directory_group",
        "qka_next_ad_directory_user",
        "qka_next_ad_ou_scope",
        "qka_next_ad_connection",
    ):
        bind.execute(text(f"DROP TABLE IF EXISTS {table}"))
