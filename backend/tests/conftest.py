from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from qka_api.auth import issue_session
from qka_api.db import create_session_factory
from qka_api.main import create_app
from qka_shared.config import get_settings
from qka_shared.google_oidc import GoogleOidcIdentity
from qka_shared.models import (
    AllowedEmail,
    ChatAccess,
    ExternalIdentity,
    GoogleWorkspaceAccount,
    QkaUser,
    SynologyApprovedShare,
    SynologyDocumentContent,
    SynologyFileInventory,
)
from tests.fake_google import FakeGoogleOidcService

TEST_URL = os.environ.get(
    "QKA_TEST_DATABASE_URL",
    "postgresql+psycopg://qka_test@127.0.0.1:55432/qka_next_test",
)
if "qka_next_test" not in TEST_URL or any(name in TEST_URL for name in ("qka_next_dev", "qka_014_frontend_it")):
    raise RuntimeError("QKA-Next tests may only use qka_next_test.")

ALLOWED_USER = uuid.UUID("aaaaaaaa-1111-4111-8111-111111111111")
DENIED_USER = uuid.UUID("bbbbbbbb-2222-4222-8222-222222222222")
DISABLED_USER = uuid.UUID("cccccccc-3333-4333-8333-333333333333")
UNLISTED_USER = uuid.UUID("dddddddd-4444-4444-8444-444444444444")
GOOGLE_ONLY_USER = uuid.UUID("aaaaaaaa-9999-4999-8999-999999999999")
SHARE_ID = uuid.UUID("533398e3-3f65-457e-adc8-abc318abb063")
FILE_ID = uuid.UUID("d68c528b-9896-48be-963f-625502cfb9e3")
CONTENT_ID = uuid.UUID("4f21b207-a4c3-49e7-b13f-962d72eac2a6")
SID_ALLOWED = "S-1-5-21-4108582196-371218740-2140778505-3104"
GOOGLE_SUB_ALLOWED = "google-sub-eduard"
GOOGLE_SUB_DENIED = "google-sub-other"
GOOGLE_SUB_DISABLED = "google-sub-disabled"
GOOGLE_SUB_UNLISTED = "google-sub-unlisted"
GOOGLE_SUB_PENDING = "google-sub-pending"
GOOGLE_SUB_UNKNOWN = "google-sub-unknown"
GOOGLE_SUB_ONLY = "google-sub-only"
ALLOW_EMAIL_EDUARD = uuid.UUID("aaaaaaaa-eeee-4eee-8eee-111111111111")
ALLOW_EMAIL_DEV = uuid.UUID("aaaaaaaa-eeee-4eee-8eee-222222222222")
ALLOW_EMAIL_OTHER = uuid.UUID("aaaaaaaa-eeee-4eee-8eee-333333333333")
ALLOW_EMAIL_DISABLED = uuid.UUID("aaaaaaaa-eeee-4eee-8eee-444444444444")


@pytest.fixture
def seed_db():
    get_settings.cache_clear()
    os.environ["QKA_DATABASE_URL"] = TEST_URL
    os.environ["QKA_DATABASE_READ_ONLY"] = "false"
    os.environ["QKA_DEV_SESSION_ENABLED"] = "false"
    os.environ["QKA_ENVIRONMENT"] = "test"
    os.environ["QKA_SESSION_SECRET"] = "test-secret-not-for-production"
    os.environ["QKA_GOOGLE_CLIENT_ID"] = "test-google-client.apps.googleusercontent.com"
    os.environ["QKA_GOOGLE_REDIRECT_URI"] = "http://127.0.0.1:5186/api/v1/auth/google/callback"
    os.environ["QKA_GOOGLE_HOSTED_DOMAIN"] = "qehc.edu.sa"
    os.environ["QKA_LOCAL_ADMIN_USERNAME"] = "super"
    os.environ["QKA_LOCAL_ADMIN_PASSWORD"] = "123"
    os.environ["QKA_PILOT_FOLDER_PREFIX"] = "EDUARDTESTCLOUD"
    # Keep pilot mode in tests so outside-prefix skip behavior stays covered.
    os.environ["QKA_INDEXING_SCOPE_MODE"] = "pilot"
    os.environ["QKA_APPROVED_SHARE_ID"] = str(SHARE_ID)
    os.environ["QKA_WORKER_EMBEDDED"] = "false"
    os.environ["QKA_CREDENTIAL_KEY"] = "test-credential-key-not-for-production"
    os.environ["QKA_OPENAI_DATA_SHARING_PERMITTED"] = "false"
    # Force empty so .env OpenAI keys cannot leak into "not configured" tests.
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["QKA_OPENAI_API_KEY"] = ""
    os.environ.pop("QKA_LEGACY_PILOT_USER_ID", None)
    os.environ.pop("QKA_ACL_ALLOW_PRINCIPAL_CONTAINS", None)
    os.environ.pop("QKA_SMB_ALLOWED_HOSTS", None)
    get_settings.cache_clear()

    settings = get_settings()
    if "qka_next_test" not in settings.database_url:
        raise RuntimeError(f"Refusing to seed tests against {settings.database_url}")
    engine, factory = create_session_factory(settings)
    now = datetime.now(timezone.utc)
    with factory() as session:
        session.execute(text("DROP TABLE IF EXISTS qka_next_local_login"))
        session.execute(text('ALTER TABLE synology_connection_settings ADD COLUMN IF NOT EXISTS "ConnectionName" varchar(256)'))
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_ai_conversation (
                    id UUID PRIMARY KEY,
                    qka_user_id UUID NOT NULL,
                    title VARCHAR(256) NOT NULL DEFAULT 'New chat',
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_ai_message (
                    id UUID PRIMARY KEY,
                    conversation_id UUID NOT NULL REFERENCES qka_next_ai_conversation(id) ON DELETE CASCADE,
                    role VARCHAR(32) NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    status VARCHAR(32) NOT NULL DEFAULT 'complete',
                    error VARCHAR(1024) NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_ai_message_attachment (
                    id UUID PRIMARY KEY,
                    message_id UUID NOT NULL REFERENCES qka_next_ai_message(id) ON DELETE CASCADE,
                    kind VARCHAR(32) NOT NULL,
                    document_id UUID NULL,
                    sample_id UUID NULL,
                    display_name VARCHAR(512) NOT NULL DEFAULT '',
                    source_ref VARCHAR(2048) NULL
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_ai_sample_upload (
                    id UUID PRIMARY KEY,
                    uploaded_by_user_id UUID NOT NULL,
                    file_name VARCHAR(512) NOT NULL,
                    extension VARCHAR(32) NOT NULL,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    extracted_text TEXT NULL,
                    content_sha256 VARCHAR(64) NULL,
                    status INTEGER NOT NULL DEFAULT 0,
                    safe_error VARCHAR(1024) NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_ai_usage_audit (
                    id UUID PRIMARY KEY,
                    qka_user_id UUID NULL,
                    conversation_id UUID NULL,
                    model VARCHAR(128) NOT NULL DEFAULT '',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    succeeded BOOLEAN NOT NULL DEFAULT FALSE,
                    error_category VARCHAR(128) NULL,
                    detail VARCHAR(512) NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_source_scope (
                    id UUID PRIMARY KEY,
                    approved_share_id UUID NOT NULL,
                    folder_prefix_normalized VARCHAR(2048) NOT NULL,
                    trust_class INTEGER NOT NULL DEFAULT 0,
                    status INTEGER NOT NULL DEFAULT 0,
                    display_name VARCHAR(512) NOT NULL DEFAULT '',
                    source_owner_name VARCHAR(256) NOT NULL DEFAULT '',
                    source_owner_email VARCHAR(256) NOT NULL DEFAULT '',
                    approval_reference VARCHAR(512) NOT NULL DEFAULT '',
                    approval_notes VARCHAR(2000) NOT NULL DEFAULT '',
                    approved_at TIMESTAMPTZ NULL,
                    approved_by_user_id UUID NULL,
                    service_read_verified BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_source_grant (
                    id UUID PRIMARY KEY,
                    scope_id UUID NOT NULL REFERENCES qka_next_source_scope(id) ON DELETE CASCADE,
                    qka_user_id UUID NULL,
                    allowed_email_id UUID NULL,
                    capability_read BOOLEAN NOT NULL DEFAULT TRUE,
                    capability_search BOOLEAN NOT NULL DEFAULT TRUE,
                    capability_preview BOOLEAN NOT NULL DEFAULT TRUE,
                    capability_view BOOLEAN NOT NULL DEFAULT FALSE,
                    capability_download BOOLEAN NOT NULL DEFAULT FALSE,
                    path_mode INTEGER NOT NULL DEFAULT 0,
                    include_subfolders BOOLEAN NOT NULL DEFAULT TRUE,
                    selected_paths_json TEXT NOT NULL DEFAULT '[]',
                    file_types_json TEXT NOT NULL DEFAULT '[]',
                    status INTEGER NOT NULL DEFAULT 0,
                    reason VARCHAR(1024) NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    created_by_user_id UUID NULL,
                    updated_by_user_id UUID NULL
                )
                """
            )
        )
        session.execute(text("ALTER TABLE qka_next_source_grant ALTER COLUMN qka_user_id DROP NOT NULL"))
        session.execute(text("ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS allowed_email_id UUID NULL"))
        session.execute(text("ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS capability_view BOOLEAN NOT NULL DEFAULT FALSE"))
        session.execute(text("ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS capability_download BOOLEAN NOT NULL DEFAULT FALSE"))
        session.execute(text("ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS path_mode INTEGER NOT NULL DEFAULT 0"))
        session.execute(text("ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS include_subfolders BOOLEAN NOT NULL DEFAULT TRUE"))
        session.execute(text("ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS selected_paths_json TEXT NOT NULL DEFAULT '[]'"))
        session.execute(text("ALTER TABLE qka_next_source_grant ADD COLUMN IF NOT EXISTS file_types_json TEXT NOT NULL DEFAULT '[]'"))
        session.execute(
            text(
                """
                UPDATE qka_next_source_grant
                SET capability_view = (capability_read OR capability_search OR capability_preview),
                    capability_download = FALSE
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_chat_access (
                    id UUID PRIMARY KEY,
                    allowed_email_id UUID UNIQUE,
                    qka_user_id UUID UNIQUE,
                    local_chat_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    mcp_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    updated_by_user_id UUID NULL
                )
                """
            )
        )
        session.execute(text("ALTER TABLE qka_next_chat_access ALTER COLUMN allowed_email_id DROP NOT NULL"))
        session.execute(text("ALTER TABLE qka_next_chat_access ADD COLUMN IF NOT EXISTS qka_user_id UUID NULL"))
        session.execute(text('ALTER TABLE qka_user ADD COLUMN IF NOT EXISTS "DirectoryDepartment" VARCHAR(512) NOT NULL DEFAULT \'\''))
        session.execute(text('ALTER TABLE qka_user ADD COLUMN IF NOT EXISTS "ContactEmail" VARCHAR(256) NULL'))
        session.execute(text('ALTER TABLE qka_user ADD COLUMN IF NOT EXISTS "McpConnectedAt" TIMESTAMPTZ NULL'))
        session.execute(text('ALTER TABLE qka_user ADD COLUMN IF NOT EXISTS "McpLastActivityAt" TIMESTAMPTZ NULL'))
        session.execute(text("ALTER TABLE qka_next_ad_directory_user ADD COLUMN IF NOT EXISTS qka_eligible BOOLEAN NOT NULL DEFAULT FALSE"))
        session.execute(text("ALTER TABLE qka_next_ad_directory_user ADD COLUMN IF NOT EXISTS qka_archived BOOLEAN NOT NULL DEFAULT FALSE"))
        session.execute(text("ALTER TABLE qka_next_ad_directory_user ADD COLUMN IF NOT EXISTS department_override VARCHAR(512) NOT NULL DEFAULT ''"))
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_source_grant_audit (
                    id UUID PRIMARY KEY,
                    action VARCHAR(64) NOT NULL,
                    actor_user_id UUID NULL,
                    target_user_id UUID NULL,
                    scope_id UUID NULL,
                    grant_id UUID NULL,
                    detail VARCHAR(2000) NOT NULL DEFAULT '',
                    employee_email VARCHAR(256) NOT NULL DEFAULT '',
                    share_name VARCHAR(256) NOT NULL DEFAULT '',
                    before_detail TEXT NOT NULL DEFAULT '',
                    after_detail TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )
        session.execute(text("ALTER TABLE qka_next_source_grant_audit ADD COLUMN IF NOT EXISTS employee_email VARCHAR(256) NOT NULL DEFAULT ''"))
        session.execute(text("ALTER TABLE qka_next_source_grant_audit ADD COLUMN IF NOT EXISTS share_name VARCHAR(256) NOT NULL DEFAULT ''"))
        session.execute(text("ALTER TABLE qka_next_source_grant_audit ADD COLUMN IF NOT EXISTS before_detail TEXT NOT NULL DEFAULT ''"))
        session.execute(text("ALTER TABLE qka_next_source_grant_audit ADD COLUMN IF NOT EXISTS after_detail TEXT NOT NULL DEFAULT ''"))
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_mcp_document_handoff (
                    id UUID PRIMARY KEY,
                    qka_user_id UUID NOT NULL,
                    inventory_file_id UUID NOT NULL,
                    purpose VARCHAR(32) NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    consumed_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_mcp_indexing_confirmation (
                    id UUID PRIMARY KEY,
                    qka_user_id UUID NOT NULL,
                    operation VARCHAR(64) NOT NULL,
                    source_id UUID NULL,
                    document_ids_json TEXT NOT NULL DEFAULT '[]',
                    expires_at TIMESTAMPTZ NOT NULL,
                    consumed_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    created_by_user_id UUID NOT NULL
                )
                """
            )
        )
        for stmt in (
            """
            ALTER TABLE IF EXISTS qka_next_ad_connection
                ALTER COLUMN protocol TYPE VARCHAR(32)
            """,
            """
            CREATE TABLE IF NOT EXISTS qka_next_ad_connection (
                id UUID PRIMARY KEY,
                connection_name VARCHAR(256) NOT NULL DEFAULT '',
                domain_dns_name VARCHAR(256) NOT NULL DEFAULT '',
                domain_controller VARCHAR(256) NOT NULL DEFAULT '',
                protocol VARCHAR(32) NOT NULL DEFAULT 'windows_integrated',
                port INTEGER NOT NULL DEFAULT 389,
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
            """,
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
            """,
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
                qka_eligible BOOLEAN NOT NULL DEFAULT FALSE,
                qka_archived BOOLEAN NOT NULL DEFAULT FALSE,
                department_override VARCHAR(512) NOT NULL DEFAULT '',
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
            """,
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
            """,
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
            """,
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
            """,
            """
            CREATE TABLE IF NOT EXISTS qka_next_ad_audit (
                id UUID PRIMARY KEY,
                connection_id UUID NULL,
                action VARCHAR(64) NOT NULL,
                actor_user_id UUID NULL,
                detail VARCHAR(2000) NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL
            )
            """,
        ):
            session.execute(text(stmt))
        session.execute(text('ALTER TABLE allowed_email ADD COLUMN IF NOT EXISTS "DisplayName" VARCHAR(256) NOT NULL DEFAULT \'\''))
        session.execute(text('ALTER TABLE allowed_email ADD COLUMN IF NOT EXISTS "GoogleProfileName" VARCHAR(256) NOT NULL DEFAULT \'\''))
        session.execute(text('ALTER TABLE allowed_email ADD COLUMN IF NOT EXISTS "DirectoryDepartment" VARCHAR(512) NOT NULL DEFAULT \'\''))
        session.execute(text('ALTER TABLE synology_file_inventory ADD COLUMN IF NOT EXISTS "IsDirectory" BOOLEAN NOT NULL DEFAULT FALSE'))
        session.execute(text('ALTER TABLE synology_file_inventory ADD COLUMN IF NOT EXISTS "ParentPathNormalized" VARCHAR(2048) NOT NULL DEFAULT \'\''))
        session.execute(text('ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "AccessModel" INTEGER NOT NULL DEFAULT 0'))
        session.execute(text('ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedApprovalReference" VARCHAR(512) NOT NULL DEFAULT \'\''))
        session.execute(text('ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedOwnerName" VARCHAR(256) NOT NULL DEFAULT \'\''))
        session.execute(text('ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedOwnerEmail" VARCHAR(256) NOT NULL DEFAULT \'\''))
        session.execute(text('ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedNotes" VARCHAR(2000) NOT NULL DEFAULT \'\''))
        session.execute(text('ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedServiceReadVerified" BOOLEAN NOT NULL DEFAULT FALSE'))
        session.execute(text('ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedEnabledAt" TIMESTAMPTZ NULL'))
        session.execute(text('ALTER TABLE synology_approved_share ADD COLUMN IF NOT EXISTS "QkaManagedEnabledByUserId" UUID NULL'))
        session.execute(text('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "ChunksJson" TEXT NULL'))
        session.execute(text('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "ExtractionMethod" VARCHAR(64) NULL'))
        session.execute(text('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "ExtractorVersion" INTEGER NOT NULL DEFAULT 0'))
        session.execute(text('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "OcrUsed" BOOLEAN NOT NULL DEFAULT FALSE'))
        session.execute(text('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "Truncated" BOOLEAN NOT NULL DEFAULT FALSE'))
        session.execute(text('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "Incomplete" BOOLEAN NOT NULL DEFAULT FALSE'))
        session.execute(text('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "AttemptCount" INTEGER NOT NULL DEFAULT 0'))
        session.execute(text('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "NextAttemptAt" TIMESTAMPTZ NULL'))
        session.execute(text('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "LeaseOwner" VARCHAR(256) NULL'))
        session.execute(text('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "LeaseExpiresAt" TIMESTAMPTZ NULL'))
        session.execute(text('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "SourceLastWriteUtc" TIMESTAMPTZ NULL'))
        session.execute(text('ALTER TABLE synology_document_content ADD COLUMN IF NOT EXISTS "ExtractionNotes" VARCHAR(2000) NULL'))
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS synology_extraction_batch (
                    "Id" UUID PRIMARY KEY,
                    "Action" VARCHAR(64) NOT NULL,
                    "Status" INTEGER NOT NULL DEFAULT 0,
                    "ActorUserId" UUID NULL,
                    "ApprovedShareId" UUID NULL,
                    "SyncJobId" UUID NULL,
                    "FolderPrefixNormalized" VARCHAR(2048) NOT NULL DEFAULT '',
                    "ForceOutsidePilot" BOOLEAN NOT NULL DEFAULT FALSE,
                    "QueuedCount" INTEGER NOT NULL DEFAULT 0,
                    "ProcessedCount" INTEGER NOT NULL DEFAULT 0,
                    "SucceededCount" INTEGER NOT NULL DEFAULT 0,
                    "FailedCount" INTEGER NOT NULL DEFAULT 0,
                    "SkippedCount" INTEGER NOT NULL DEFAULT 0,
                    "UnsupportedCount" INTEGER NOT NULL DEFAULT 0,
                    "CancelRequested" BOOLEAN NOT NULL DEFAULT FALSE,
                    "SafeError" VARCHAR(1024) NULL,
                    "CreatedAt" TIMESTAMPTZ NOT NULL,
                    "StartedAt" TIMESTAMPTZ NULL,
                    "CompletedAt" TIMESTAMPTZ NULL
                )
                """
            )
        )
        session.execute(text('ALTER TABLE synology_extraction_batch ADD COLUMN IF NOT EXISTS "SyncJobId" UUID NULL'))
        session.execute(text('ALTER TABLE synology_sync_job ADD COLUMN IF NOT EXISTS "ExtractionBatchId" UUID NULL'))
        session.execute(
            text(
                "ALTER TABLE synology_sync_job ADD COLUMN IF NOT EXISTS \"RunStage\" VARCHAR(32) NOT NULL DEFAULT 'discovering'"
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS synology_extraction_queue (
                    "Id" UUID PRIMARY KEY,
                    "BatchId" UUID NULL,
                    "InventoryFileId" UUID NOT NULL,
                    "ApprovedShareId" UUID NOT NULL,
                    "Status" INTEGER NOT NULL DEFAULT 0,
                    "ForceOutsidePilot" BOOLEAN NOT NULL DEFAULT FALSE,
                    "AttemptCount" INTEGER NOT NULL DEFAULT 0,
                    "Priority" INTEGER NOT NULL DEFAULT 100,
                    "LeaseOwner" VARCHAR(256) NULL,
                    "LeaseExpiresAt" TIMESTAMPTZ NULL,
                    "NextAttemptAt" TIMESTAMPTZ NULL,
                    "SafeError" VARCHAR(1024) NULL,
                    "CreatedAt" TIMESTAMPTZ NOT NULL,
                    "UpdatedAt" TIMESTAMPTZ NOT NULL
                )
                """
            )
        )
        session.execute(text("DELETE FROM synology_extraction_queue"))
        session.execute(text("DELETE FROM synology_extraction_batch"))
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_chatgpt_membership (
                    id UUID PRIMARY KEY,
                    allowed_email_id UUID NOT NULL UNIQUE,
                    status INTEGER NOT NULL DEFAULT 0,
                    source VARCHAR(64) NOT NULL DEFAULT 'unknown',
                    member_name VARCHAR(256) NOT NULL DEFAULT '',
                    workspace_role VARCHAR(64) NOT NULL DEFAULT '',
                    seat_type VARCHAR(64) NOT NULL DEFAULT '',
                    verified_at TIMESTAMPTZ NULL,
                    last_import_batch_id UUID NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    updated_by_user_id UUID NULL
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS qka_next_membership_import_batch (
                    id UUID PRIMARY KEY,
                    source_label VARCHAR(256) NOT NULL DEFAULT 'csv_import',
                    file_name VARCHAR(512) NOT NULL DEFAULT '',
                    row_count INTEGER NOT NULL DEFAULT 0,
                    matched_count INTEGER NOT NULL DEFAULT 0,
                    created_count INTEGER NOT NULL DEFAULT 0,
                    member_count INTEGER NOT NULL DEFAULT 0,
                    nonmember_count INTEGER NOT NULL DEFAULT 0,
                    mark_missing_nonmember BOOLEAN NOT NULL DEFAULT FALSE,
                    notes VARCHAR(2000) NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL,
                    created_by_user_id UUID NULL
                )
                """
            )
        )
        session.execute(text("DELETE FROM qka_next_chatgpt_membership"))
        session.execute(text("DELETE FROM qka_next_membership_import_batch"))
        session.execute(text("DELETE FROM qka_next_source_grant_audit"))
        session.execute(text("DELETE FROM qka_next_source_grant"))
        session.execute(text("DELETE FROM qka_next_source_scope"))
        session.execute(text("DELETE FROM qka_next_chat_access"))
        session.execute(text("DELETE FROM qka_next_ad_group_member"))
        session.execute(text("DELETE FROM qka_next_ad_directory_group"))
        session.execute(text("DELETE FROM qka_next_ad_directory_user"))
        session.execute(text("DELETE FROM qka_next_ad_ou_scope"))
        session.execute(text("DELETE FROM qka_next_ad_sync_job"))
        session.execute(text("DELETE FROM qka_next_ad_audit"))
        session.execute(text("DELETE FROM qka_next_ad_connection"))
        session.execute(text("DELETE FROM qka_next_ai_usage_audit"))
        session.execute(text("DELETE FROM qka_next_ai_message_attachment"))
        session.execute(text("DELETE FROM qka_next_ai_message"))
        session.execute(text("DELETE FROM qka_next_ai_conversation"))
        session.execute(text("DELETE FROM qka_next_ai_sample_upload"))
        session.execute(text("DELETE FROM synology_document_content"))
        session.execute(text("DELETE FROM synology_file_inventory"))
        session.execute(text("DELETE FROM synology_sync_job"))
        session.execute(text("DELETE FROM synology_sync_job_lease"))
        session.execute(text("DELETE FROM synology_sync_settings"))
        session.execute(text("DELETE FROM synology_connection_settings"))
        session.execute(text("DELETE FROM synology_approved_share"))
        session.execute(text("DELETE FROM allowed_email"))
        session.execute(text("DELETE FROM google_workspace_account"))
        session.execute(text("DELETE FROM external_identity"))
        session.execute(text("DELETE FROM qka_user"))
        session.add_all(
            [
                QkaUser(id=ALLOWED_USER, display_name="Allowed Pilot", status=0, login_enabled=True, is_directory_operator=False, is_super_admin=False, created_at=now, updated_at=now),
                QkaUser(id=DENIED_USER, display_name="Denied User", status=0, login_enabled=True, is_directory_operator=False, is_super_admin=False, created_at=now, updated_at=now),
                QkaUser(id=DISABLED_USER, display_name="Disabled Email User", status=0, login_enabled=True, is_directory_operator=False, is_super_admin=False, created_at=now, updated_at=now),
                QkaUser(id=UNLISTED_USER, display_name="Unlisted User", status=0, login_enabled=True, is_directory_operator=False, is_super_admin=False, created_at=now, updated_at=now),
                QkaUser(id=GOOGLE_ONLY_USER, display_name="Google Only Employee", status=0, login_enabled=True, is_directory_operator=False, is_super_admin=False, created_at=now, updated_at=now),
                ExternalIdentity(id=uuid.UUID("aaaaaaaa-2222-4222-8222-222222222222"), qka_user_id=ALLOWED_USER, provider=0, stable_external_id=SID_ALLOWED, domain_username="qkai", primary_email="eduard@qehc.edu.sa", account_status=0, link_status=2, created_at=now, updated_at=now),
                ExternalIdentity(id=uuid.UUID("aaaaaaaa-3333-4333-8333-333333333333"), qka_user_id=ALLOWED_USER, provider=1, stable_external_id=GOOGLE_SUB_ALLOWED, primary_email="eduard@qehc.edu.sa", account_status=0, link_status=2, created_at=now, updated_at=now),
                ExternalIdentity(id=uuid.UUID("bbbbbbbb-3333-4333-8333-333333333333"), qka_user_id=DENIED_USER, provider=0, stable_external_id="other-sid", domain_username="otheruser", primary_email="other.person@qehc.edu.sa", account_status=0, link_status=2, created_at=now, updated_at=now),
                ExternalIdentity(id=uuid.UUID("bbbbbbbb-4444-4444-8444-444444444444"), qka_user_id=DENIED_USER, provider=1, stable_external_id=GOOGLE_SUB_DENIED, primary_email="other.person@qehc.edu.sa", account_status=0, link_status=2, created_at=now, updated_at=now),
                ExternalIdentity(id=uuid.UUID("cccccccc-4444-4444-8444-444444444444"), qka_user_id=DISABLED_USER, provider=1, stable_external_id=GOOGLE_SUB_DISABLED, primary_email="disabled.user@qehc.edu.sa", account_status=0, link_status=2, created_at=now, updated_at=now),
                ExternalIdentity(id=uuid.UUID("dddddddd-5555-4555-8555-555555555555"), qka_user_id=UNLISTED_USER, provider=1, stable_external_id=GOOGLE_SUB_UNLISTED, primary_email="unlisted.user@qehc.edu.sa", account_status=0, link_status=2, created_at=now, updated_at=now),
                ExternalIdentity(id=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), qka_user_id=GOOGLE_ONLY_USER, provider=1, stable_external_id=GOOGLE_SUB_ONLY, primary_email="eduard@qehc.edu.sa", account_status=0, link_status=2, created_at=now, updated_at=now),
                ExternalIdentity(id=uuid.UUID("eeeeeeee-6666-4666-8666-666666666666"), qka_user_id=None, provider=1, stable_external_id=GOOGLE_SUB_PENDING, primary_email="pending.user@qehc.edu.sa", account_status=0, link_status=0, created_at=now, updated_at=now),
                AllowedEmail(id=ALLOW_EMAIL_EDUARD, email="eduard@qehc.edu.sa", email_normalized="eduard@qehc.edu.sa", enabled=True, display_name="Eduard Pilot", google_profile_name="", created_at=now, updated_at=now),
                AllowedEmail(id=ALLOW_EMAIL_DEV, email="dev.eduard@qehc.edu.sa", email_normalized="dev.eduard@qehc.edu.sa", enabled=True, display_name="Dev Eduard", google_profile_name="", created_at=now, updated_at=now),
                AllowedEmail(id=ALLOW_EMAIL_OTHER, email="other.person@qehc.edu.sa", email_normalized="other.person@qehc.edu.sa", enabled=True, display_name="Other Person", google_profile_name="", created_at=now, updated_at=now),
                AllowedEmail(id=ALLOW_EMAIL_DISABLED, email="disabled.user@qehc.edu.sa", email_normalized="disabled.user@qehc.edu.sa", enabled=False, display_name="Disabled User", google_profile_name="", created_at=now, updated_at=now),
                # Default channel flags: Local AI + MCP enabled for the pilot allowlist used by AI tests.
                ChatAccess(id=uuid.uuid4(), allowed_email_id=ALLOW_EMAIL_EDUARD, local_chat_enabled=True, mcp_enabled=True, created_at=now, updated_at=now, updated_by_user_id=None),
                # Local chat on for ACL-denial chat tests; MCP stays off for channel isolation tests.
                ChatAccess(id=uuid.uuid4(), allowed_email_id=ALLOW_EMAIL_OTHER, local_chat_enabled=True, mcp_enabled=False, created_at=now, updated_at=now, updated_by_user_id=None),
                SynologyApprovedShare(id=SHARE_ID, share_name="eduard", share_name_normalized="EDUARD", folder_relative_path="", folder_relative_path_normalized="", is_active=True, acl_capture_status=1, created_at=now, updated_at=now),
                SynologyFileInventory(
                    id=FILE_ID,
                    approved_share_id=SHARE_ID,
                    relative_path=r"EDUARDTESTCLOUD\Assets New Form.xlsx",
                    relative_path_normalized=r"EDUARDTESTCLOUD\ASSETS NEW FORM.XLSX",
                    file_name="Assets New Form.xlsx",
                    extension="xlsx",
                    file_type_category=1,
                    size_bytes=100,
                    sync_status=0,
                    acl_capture_status=1,
                    acl_snapshot_json=f'[{{"Identity":"{SID_ALLOWED}","Rights":"Modify, Read","AccessControlType":"Allow"}}]',
                    is_present=True,
                    is_directory=False,
                    parent_path_normalized=r"EDUARDTESTCLOUD",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                GoogleWorkspaceAccount(id=uuid.uuid4(), external_identity_id=uuid.UUID("aaaaaaaa-3333-4333-8333-333333333333"), google_user_id=GOOGLE_SUB_ALLOWED, primary_email="eduard@qehc.edu.sa", drive_onboarded=False),
                GoogleWorkspaceAccount(id=uuid.uuid4(), external_identity_id=uuid.UUID("bbbbbbbb-4444-4444-8444-444444444444"), google_user_id=GOOGLE_SUB_DENIED, primary_email="other.person@qehc.edu.sa", drive_onboarded=False),
                GoogleWorkspaceAccount(id=uuid.uuid4(), external_identity_id=uuid.UUID("cccccccc-4444-4444-8444-444444444444"), google_user_id=GOOGLE_SUB_DISABLED, primary_email="disabled.user@qehc.edu.sa", drive_onboarded=False),
                GoogleWorkspaceAccount(id=uuid.uuid4(), external_identity_id=uuid.UUID("dddddddd-5555-4555-8555-555555555555"), google_user_id=GOOGLE_SUB_UNLISTED, primary_email="unlisted.user@qehc.edu.sa", drive_onboarded=False),
                GoogleWorkspaceAccount(id=uuid.uuid4(), external_identity_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), google_user_id=GOOGLE_SUB_ONLY, primary_email="eduard@qehc.edu.sa", drive_onboarded=False),
                GoogleWorkspaceAccount(id=uuid.uuid4(), external_identity_id=uuid.UUID("eeeeeeee-6666-4666-8666-666666666666"), google_user_id=GOOGLE_SUB_PENDING, primary_email="pending.user@qehc.edu.sa", drive_onboarded=False),
                SynologyDocumentContent(
                    id=CONTENT_ID,
                    inventory_file_id=FILE_ID,
                    approved_share_id=SHARE_ID,
                    relative_path_normalized=r"EDUARDTESTCLOUD\ASSETS NEW FORM.XLSX",
                    file_name="Assets New Form.xlsx",
                    extension="xlsx",
                    status=1,
                    extracted_text="Laptop HP Laptop 15s-qe2022nx assets inventory",
                    pilot_scoped=True,
                    non_confidential=True,
                    extracted_at=now,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        session.commit()
        from qka_api.services.inventory_hierarchy import backfill_inventory_hierarchy

        backfill_inventory_hierarchy(session)
        session.commit()
        session.execute(text("""UPDATE synology_document_content SET search_vector = to_tsvector('simple', coalesce("ExtractedText", ''))"""))
        session.commit()
    yield
    engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
def client(seed_db):
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def google_client(client):
    client.app.state.google_oidc = FakeGoogleOidcService(
        {
            "code-allowed": GoogleOidcIdentity(GOOGLE_SUB_ALLOWED, "eduard@qehc.edu.sa", True, "qehc.edu.sa", "Eduard"),
            "code-denied": GoogleOidcIdentity(GOOGLE_SUB_DENIED, "other.person@qehc.edu.sa", True, "qehc.edu.sa", "Other"),
            "code-disabled": GoogleOidcIdentity(GOOGLE_SUB_DISABLED, "disabled.user@qehc.edu.sa", True, "qehc.edu.sa", "Disabled"),
            "code-unlisted": GoogleOidcIdentity(GOOGLE_SUB_UNLISTED, "unlisted.user@qehc.edu.sa", True, "qehc.edu.sa", "Unlisted"),
            "code-pending": GoogleOidcIdentity(GOOGLE_SUB_PENDING, "pending.user@qehc.edu.sa", True, "qehc.edu.sa", "Pending"),
            "code-unknown": GoogleOidcIdentity(GOOGLE_SUB_UNKNOWN, "eduard@qehc.edu.sa", True, "qehc.edu.sa", "Eduard"),
            "code-gmail": GoogleOidcIdentity("sub-gmail", "user@gmail.com", True, "gmail.com", "Gmail"),
            "code-unverified": GoogleOidcIdentity("sub-unverified", "eduard@qehc.edu.sa", False, "qehc.edu.sa", "Unverified"),
        }
    )
    return client


def auth_cookies(user_id: uuid.UUID) -> dict[str, str]:
    get_settings.cache_clear()
    return {"qka_next_session": issue_session(get_settings(), user_id)}
