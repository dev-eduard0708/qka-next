from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class QkaUser(Base):
    __tablename__ = "qka_user"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    display_name: Mapped[str] = mapped_column("DisplayName", String(256))
    status: Mapped[int] = mapped_column("Status", Integer, default=0)
    effective_department_id: Mapped[uuid.UUID | None] = mapped_column("EffectiveDepartmentId", Uuid(as_uuid=True), nullable=True)
    # Admin-assigned directory department (AD override or Google enrollment). Not overwritten by AD resync.
    directory_department: Mapped[str] = mapped_column("DirectoryDepartment", String(512), default="")
    # Optional contact email (may differ from verified Google identity email).
    contact_email: Mapped[str | None] = mapped_column("ContactEmail", String(256), nullable=True)
    login_enabled: Mapped[bool] = mapped_column("LoginEnabled", Boolean, default=False)
    is_directory_operator: Mapped[bool] = mapped_column("IsDirectoryOperator", Boolean, default=False)
    is_super_admin: Mapped[bool] = mapped_column("IsSuperAdmin", Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime(timezone=True))
    # First successful MCP OAuth token issue for this user (ChatGPT connector).
    mcp_connected_at: Mapped[datetime | None] = mapped_column("McpConnectedAt", DateTime(timezone=True), nullable=True)
    # Last successful MCP tool activity for this user.
    mcp_last_activity_at: Mapped[datetime | None] = mapped_column(
        "McpLastActivityAt", DateTime(timezone=True), nullable=True
    )

    identities: Mapped[list[ExternalIdentity]] = relationship(back_populates="user")
    memberships: Mapped[list[DepartmentMembership]] = relationship(back_populates="user")


class ExternalIdentity(Base):
    __tablename__ = "external_identity"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    qka_user_id: Mapped[uuid.UUID | None] = mapped_column("QkaUserId", Uuid(as_uuid=True), ForeignKey("qka_user.Id"), nullable=True)
    provider: Mapped[int] = mapped_column("Provider", Integer)
    stable_external_id: Mapped[str] = mapped_column("StableExternalId", String(128))
    domain_username: Mapped[str | None] = mapped_column("DomainUsername", String(256), nullable=True)
    upn: Mapped[str | None] = mapped_column("Upn", String(256), nullable=True)
    primary_email: Mapped[str | None] = mapped_column("PrimaryEmail", String(256), nullable=True)
    account_status: Mapped[int] = mapped_column("AccountStatus", Integer, default=0)
    link_status: Mapped[int] = mapped_column("LinkStatus", Integer, default=0)
    directory_presence: Mapped[int] = mapped_column("DirectoryPresence", Integer, default=0)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime(timezone=True))

    user: Mapped[QkaUser | None] = relationship(back_populates="identities")
    google: Mapped[GoogleWorkspaceAccount | None] = relationship(back_populates="identity")


class GoogleWorkspaceAccount(Base):
    __tablename__ = "google_workspace_account"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    external_identity_id: Mapped[uuid.UUID] = mapped_column("ExternalIdentityId", Uuid(as_uuid=True), ForeignKey("external_identity.Id"))
    google_user_id: Mapped[str] = mapped_column("GoogleUserId", String(128))
    primary_email: Mapped[str] = mapped_column("PrimaryEmail", String(256))
    drive_onboarded: Mapped[bool] = mapped_column("DriveOnboarded", Boolean, default=False)

    identity: Mapped[ExternalIdentity] = relationship(back_populates="google")


class Department(Base):
    __tablename__ = "department"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column("Code", String(64), default="")
    name: Mapped[str] = mapped_column("Name", String(256), default="")
    origin: Mapped[str] = mapped_column("Origin", String(64), default="qka")
    status: Mapped[int] = mapped_column("Status", Integer, default=0)
    created_at: Mapped[datetime | None] = mapped_column("CreatedAt", DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column("UpdatedAt", DateTime(timezone=True), nullable=True)


class DepartmentMembership(Base):
    __tablename__ = "department_membership"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    qka_user_id: Mapped[uuid.UUID] = mapped_column("QkaUserId", Uuid(as_uuid=True), ForeignKey("qka_user.Id"))
    department_id: Mapped[uuid.UUID | None] = mapped_column("DepartmentId", Uuid(as_uuid=True), ForeignKey("department.Id"), nullable=True)

    user: Mapped[QkaUser] = relationship(back_populates="memberships")


class AdSecurityGroup(Base):
    __tablename__ = "ad_security_group"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    is_active: Mapped[bool] = mapped_column("IsActive", Boolean, default=True)


class AdGroupMembership(Base):
    __tablename__ = "ad_group_membership"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    external_identity_id: Mapped[uuid.UUID] = mapped_column("ExternalIdentityId", Uuid(as_uuid=True), ForeignKey("external_identity.Id"))
    ad_security_group_id: Mapped[uuid.UUID] = mapped_column("AdSecurityGroupId", Uuid(as_uuid=True), ForeignKey("ad_security_group.Id"))


class AccessPolicy(Base):
    __tablename__ = "access_policy"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    effect: Mapped[int] = mapped_column("Effect", Integer)
    scope_kind: Mapped[int] = mapped_column("ScopeKind", Integer)
    resource_id: Mapped[uuid.UUID | None] = mapped_column("ResourceId", Uuid(as_uuid=True), nullable=True)
    owner_qka_user_id: Mapped[uuid.UUID | None] = mapped_column("OwnerQkaUserId", Uuid(as_uuid=True), nullable=True)
    target_kind: Mapped[int] = mapped_column("TargetKind", Integer)
    target_id: Mapped[uuid.UUID] = mapped_column("TargetId", Uuid(as_uuid=True))
    status: Mapped[int] = mapped_column("Status", Integer, default=0)


class AllowedEmail(Base):
    __tablename__ = "allowed_email"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column("Email", String(256))
    email_normalized: Mapped[str] = mapped_column("EmailNormalized", String(256))
    enabled: Mapped[bool] = mapped_column("Enabled", Boolean, default=True)
    # Admin-provided directory name (never overwritten by Google profile).
    display_name: Mapped[str] = mapped_column("DisplayName", String(256), default="")
    # Google-provided profile name captured on verified bind (informational).
    google_profile_name: Mapped[str] = mapped_column("GoogleProfileName", String(256), default="")
    # QKA directory department for allowlist-only (pending Google) rows.
    directory_department: Mapped[str] = mapped_column("DirectoryDepartment", String(512), default="")
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column("CreatedByUserId", Uuid(as_uuid=True), nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column("UpdatedByUserId", Uuid(as_uuid=True), nullable=True)


class ChatAccess(Base):
    """QKA chat channel flags (Local AI Chat / ChatGPT MCP). Not ChatGPT seat assignment.

    Rows are keyed by AllowedEmail (Google allowlist path) and/or QkaUser (AD enrollment path).
    """

    __tablename__ = "qka_next_chat_access"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    allowed_email_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), unique=True, index=True, nullable=True)
    qka_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), unique=True, index=True, nullable=True)
    local_chat_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mcp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class ChatgptMembership(Base):
    """Informational ChatGPT Business membership badge (manual CSV import for Business plan)."""

    __tablename__ = "qka_next_chatgpt_membership"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    allowed_email_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), unique=True, index=True)
    # 0=unknown, 1=verified_member, 2=verified_nonmember
    status: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(64), default="unknown")
    member_name: Mapped[str] = mapped_column(String(256), default="")
    workspace_role: Mapped[str] = mapped_column(String(64), default="")
    seat_type: Mapped[str] = mapped_column(String(64), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_import_batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class MembershipImportBatch(Base):
    __tablename__ = "qka_next_membership_import_batch"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_label: Mapped[str] = mapped_column(String(256), default="csv_import")
    file_name: Mapped[str] = mapped_column(String(512), default="")
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    nonmember_count: Mapped[int] = mapped_column(Integer, default=0)
    mark_missing_nonmember: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class LocalLogin(Base):
    __tablename__ = "qka_next_local_login"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    qka_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("qka_user.Id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class McpToolAudit(Base):
    """Append-only MCP tool execution audit for QKA-Next (no secrets / no document bodies)."""

    __tablename__ = "qka_next_mcp_tool_audit"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    qka_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)
    error_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class McpDocumentHandoff(Base):
    """Short-lived browser handoff for MCP download/preview (bound to QKA user + document)."""

    __tablename__ = "qka_next_mcp_document_handoff"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    qka_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    inventory_file_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    purpose: Mapped[str] = mapped_column(String(32))  # download | preview
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class McpIndexingConfirmation(Base):
    """Super Admin UI confirmation required before MCP may start indexing jobs."""

    __tablename__ = "qka_next_mcp_indexing_confirmation"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    qka_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    operation: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    document_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))


class SourceScope(Base):
    """Approved Synology folder scope with trust classification (personal vs corporate designated)."""

    __tablename__ = "qka_next_source_scope"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    approved_share_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    folder_prefix_normalized: Mapped[str] = mapped_column(String(2048))
    trust_class: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=0)
    display_name: Mapped[str] = mapped_column(String(512), default="")
    source_owner_name: Mapped[str] = mapped_column(String(256), default="")
    source_owner_email: Mapped[str] = mapped_column(String(256), default="")
    approval_reference: Mapped[str] = mapped_column(String(512), default="")
    approval_notes: Mapped[str] = mapped_column(String(2000), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    service_read_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    grants: Mapped[list["SourceGrant"]] = relationship(back_populates="scope", cascade="all, delete-orphan")


class SourceGrant(Base):
    """Per-user View Documents / Download Originals grant on a source scope."""

    __tablename__ = "qka_next_source_grant"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    scope_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("qka_next_source_scope.id", ondelete="CASCADE"), index=True
    )
    qka_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True, nullable=True)
    allowed_email_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True, nullable=True)
    # Legacy columns retained for historical rows; runtime uses view/download.
    capability_read: Mapped[bool] = mapped_column(Boolean, default=True)
    capability_search: Mapped[bool] = mapped_column(Boolean, default=True)
    capability_preview: Mapped[bool] = mapped_column(Boolean, default=True)
    capability_view: Mapped[bool] = mapped_column(Boolean, default=False)
    capability_download: Mapped[bool] = mapped_column(Boolean, default=False)
    # 0 = entire corporate scope; 1 = selected paths only
    path_mode: Mapped[int] = mapped_column(Integer, default=0)
    include_subfolders: Mapped[bool] = mapped_column(Boolean, default=True)
    selected_paths_json: Mapped[str] = mapped_column(Text, default="[]")
    # Empty list means all file types; otherwise intersection filter (excel/word/pdf/…)
    file_types_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    scope: Mapped[SourceScope] = relationship(back_populates="grants")


class SourceGrantAudit(Base):
    """Append-only audit of Super Admin source access configuration actions."""

    __tablename__ = "qka_next_source_grant_audit"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    action: Mapped[str] = mapped_column(String(64))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    grant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    detail: Mapped[str] = mapped_column(String(2000), default="")
    employee_email: Mapped[str] = mapped_column(String(256), default="")
    share_name: Mapped[str] = mapped_column(String(256), default="")
    before_detail: Mapped[str] = mapped_column(Text, default="")
    after_detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SynologyConnectionSettings(Base):
    __tablename__ = "synology_connection_settings"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    host_name: Mapped[str] = mapped_column("HostName", String(256), default="")
    connection_name: Mapped[str | None] = mapped_column("ConnectionName", String(256), nullable=True)
    configured_shared_folder_path: Mapped[str] = mapped_column("ConfiguredSharedFolderPath", String(1024), default="")
    primary_approved_share_id: Mapped[uuid.UUID | None] = mapped_column("PrimaryApprovedShareId", Uuid(as_uuid=True), nullable=True)
    auth_mode: Mapped[int] = mapped_column("AuthMode", Integer, default=0)
    service_account_user_name: Mapped[str | None] = mapped_column("ServiceAccountUserName", String(256), nullable=True)
    service_account_secret_protected: Mapped[bytes | None] = mapped_column("ServiceAccountSecretProtected", LargeBinary, nullable=True)
    connection_status: Mapped[int] = mapped_column("ConnectionStatus", Integer, default=0)
    port445_reachable: Mapped[bool | None] = mapped_column("Port445Reachable", Boolean, nullable=True)
    authenticated_share_access: Mapped[bool | None] = mapped_column("AuthenticatedShareAccess", Boolean, nullable=True)
    last_successful_test_at: Mapped[datetime | None] = mapped_column("LastSuccessfulTestAt", DateTime(timezone=True), nullable=True)
    last_safe_error: Mapped[str | None] = mapped_column("LastSafeError", String(1024), nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column("UpdatedByUserId", Uuid(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime(timezone=True))


class SynologyApprovedShare(Base):
    __tablename__ = "synology_approved_share"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    share_name: Mapped[str] = mapped_column("ShareName", String(128))
    share_name_normalized: Mapped[str] = mapped_column("ShareNameNormalized", String(128))
    folder_relative_path: Mapped[str] = mapped_column("FolderRelativePath", String(1024), default="")
    folder_relative_path_normalized: Mapped[str] = mapped_column("FolderRelativePathNormalized", String(1024), default="")
    is_active: Mapped[bool] = mapped_column("IsActive", Boolean, default=True)
    acl_capture_status: Mapped[int] = mapped_column("AclCaptureStatus", Integer, default=0)
    last_safe_error: Mapped[str | None] = mapped_column("LastSafeError", String(1024), nullable=True)
    last_access_test_at: Mapped[datetime | None] = mapped_column("LastAccessTestAt", DateTime(timezone=True), nullable=True)
    last_preview_at: Mapped[datetime | None] = mapped_column("LastPreviewAt", DateTime(timezone=True), nullable=True)
    last_successful_sync_at: Mapped[datetime | None] = mapped_column("LastSuccessfulSyncAt", DateTime(timezone=True), nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column("UpdatedByUserId", Uuid(as_uuid=True), nullable=True)
    # 0 = Source ACL (AD/NAS); 1 = QKA Managed (Google identity + explicit grants). Default stays Source ACL.
    access_model: Mapped[int] = mapped_column("AccessModel", Integer, default=0)
    qka_managed_approval_reference: Mapped[str] = mapped_column("QkaManagedApprovalReference", String(512), default="")
    qka_managed_owner_name: Mapped[str] = mapped_column("QkaManagedOwnerName", String(256), default="")
    qka_managed_owner_email: Mapped[str] = mapped_column("QkaManagedOwnerEmail", String(256), default="")
    qka_managed_notes: Mapped[str] = mapped_column("QkaManagedNotes", String(2000), default="")
    qka_managed_service_read_verified: Mapped[bool] = mapped_column("QkaManagedServiceReadVerified", Boolean, default=False)
    qka_managed_enabled_at: Mapped[datetime | None] = mapped_column("QkaManagedEnabledAt", DateTime(timezone=True), nullable=True)
    qka_managed_enabled_by_user_id: Mapped[uuid.UUID | None] = mapped_column("QkaManagedEnabledByUserId", Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime(timezone=True))


class SynologySyncSettings(Base):
    __tablename__ = "synology_sync_settings"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    current_job_id: Mapped[uuid.UUID | None] = mapped_column("CurrentJobId", Uuid(as_uuid=True), nullable=True)
    last_successful_job_id: Mapped[uuid.UUID | None] = mapped_column("LastSuccessfulJobId", Uuid(as_uuid=True), nullable=True)
    last_successful_sync_at: Mapped[datetime | None] = mapped_column("LastSuccessfulSyncAt", DateTime(timezone=True), nullable=True)
    last_attempted_sync_at: Mapped[datetime | None] = mapped_column("LastAttemptedSyncAt", DateTime(timezone=True), nullable=True)
    last_excel_count: Mapped[int] = mapped_column("LastExcelCount", Integer, default=0)
    last_pdf_count: Mapped[int] = mapped_column("LastPdfCount", Integer, default=0)
    last_word_count: Mapped[int] = mapped_column("LastWordCount", Integer, default=0)
    last_powerpoint_count: Mapped[int] = mapped_column("LastPowerPointCount", Integer, default=0)
    last_csv_count: Mapped[int] = mapped_column("LastCsvCount", Integer, default=0)
    last_other_count: Mapped[int] = mapped_column("LastOtherCount", Integer, default=0)
    last_total_files: Mapped[int] = mapped_column("LastTotalFiles", Integer, default=0)
    last_total_bytes: Mapped[int] = mapped_column("LastTotalBytes", BigInteger, default=0)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column("UpdatedByUserId", Uuid(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime(timezone=True))


class SynologySyncJob(Base):
    __tablename__ = "synology_sync_job"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    trigger: Mapped[int] = mapped_column("Trigger", Integer, default=0)
    status: Mapped[int] = mapped_column("Status", Integer, default=0)
    approved_share_id: Mapped[uuid.UUID | None] = mapped_column("ApprovedShareId", Uuid(as_uuid=True), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column("ActorUserId", Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column("StartedAt", DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column("CompletedAt", DateTime(timezone=True), nullable=True)
    enumeration_complete: Mapped[bool] = mapped_column("EnumerationComplete", Boolean, default=False)
    current_operation: Mapped[str | None] = mapped_column("CurrentOperation", String(512), nullable=True)
    scope_unc_root: Mapped[str] = mapped_column("ScopeUncRoot", String(1024), default="")
    files_discovered: Mapped[int] = mapped_column("FilesDiscovered", Integer, default=0)
    files_processed: Mapped[int] = mapped_column("FilesProcessed", Integer, default=0)
    files_skipped: Mapped[int] = mapped_column("FilesSkipped", Integer, default=0)
    files_failed: Mapped[int] = mapped_column("FilesFailed", Integer, default=0)
    files_new: Mapped[int] = mapped_column("FilesNew", Integer, default=0)
    files_modified: Mapped[int] = mapped_column("FilesModified", Integer, default=0)
    files_unchanged: Mapped[int] = mapped_column("FilesUnchanged", Integer, default=0)
    files_removed: Mapped[int] = mapped_column("FilesRemoved", Integer, default=0)
    excel_count: Mapped[int] = mapped_column("ExcelCount", Integer, default=0)
    pdf_count: Mapped[int] = mapped_column("PdfCount", Integer, default=0)
    word_count: Mapped[int] = mapped_column("WordCount", Integer, default=0)
    powerpoint_count: Mapped[int] = mapped_column("PowerPointCount", Integer, default=0)
    csv_count: Mapped[int] = mapped_column("CsvCount", Integer, default=0)
    other_count: Mapped[int] = mapped_column("OtherCount", Integer, default=0)
    total_bytes: Mapped[int] = mapped_column("TotalBytes", BigInteger, default=0)
    discovery_total: Mapped[int | None] = mapped_column("DiscoveryTotal", Integer, nullable=True)
    safe_error: Mapped[str | None] = mapped_column("SafeError", String(1024), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column("CancelRequested", Boolean, default=False)
    extraction_batch_id: Mapped[uuid.UUID | None] = mapped_column("ExtractionBatchId", Uuid(as_uuid=True), nullable=True)
    # discovering | indexing | completed | failed | cancelled
    run_stage: Mapped[str] = mapped_column("RunStage", String(32), default="discovering")


class SynologySyncJobLease(Base):
    __tablename__ = "synology_sync_job_lease"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    owner_job_id: Mapped[uuid.UUID | None] = mapped_column("OwnerJobId", Uuid(as_uuid=True), nullable=True)
    owner_instance_id: Mapped[str | None] = mapped_column("OwnerInstanceId", String(256), nullable=True)
    acquired_at: Mapped[datetime | None] = mapped_column("AcquiredAt", DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column("ExpiresAt", DateTime(timezone=True), nullable=True)


class SynologyFileInventory(Base):
    __tablename__ = "synology_file_inventory"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    approved_share_id: Mapped[uuid.UUID] = mapped_column("ApprovedShareId", Uuid(as_uuid=True))
    relative_path: Mapped[str] = mapped_column("RelativePath", String(2048))
    relative_path_normalized: Mapped[str] = mapped_column("RelativePathNormalized", String(2048))
    file_name: Mapped[str] = mapped_column("FileName", String(512))
    extension: Mapped[str] = mapped_column("Extension", String(32))
    file_type_category: Mapped[int] = mapped_column("FileTypeCategory", Integer, default=0)
    size_bytes: Mapped[int] = mapped_column("SizeBytes", BigInteger, default=0)
    last_write_utc: Mapped[datetime | None] = mapped_column("LastWriteUtc", DateTime(timezone=True), nullable=True)
    sync_status: Mapped[int] = mapped_column("SyncStatus", Integer, default=0)
    acl_capture_status: Mapped[int] = mapped_column("AclCaptureStatus", Integer, default=0)
    acl_snapshot_json: Mapped[str | None] = mapped_column("AclSnapshotJson", String(8000), nullable=True)
    last_seen_job_id: Mapped[uuid.UUID | None] = mapped_column("LastSeenJobId", Uuid(as_uuid=True), nullable=True)
    is_present: Mapped[bool] = mapped_column("IsPresent", Boolean, default=True)
    is_directory: Mapped[bool] = mapped_column("IsDirectory", Boolean, default=False)
    parent_path_normalized: Mapped[str] = mapped_column("ParentPathNormalized", String(2048), default="")
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime(timezone=True))


class SynologyDocumentContent(Base):
    __tablename__ = "synology_document_content"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    inventory_file_id: Mapped[uuid.UUID] = mapped_column("InventoryFileId", Uuid(as_uuid=True))
    approved_share_id: Mapped[uuid.UUID] = mapped_column("ApprovedShareId", Uuid(as_uuid=True))
    relative_path_normalized: Mapped[str] = mapped_column("RelativePathNormalized", String(2048))
    file_name: Mapped[str] = mapped_column("FileName", String(512))
    extension: Mapped[str] = mapped_column("Extension", String(32))
    status: Mapped[int] = mapped_column("Status", Integer, default=0)
    extracted_text: Mapped[str | None] = mapped_column("ExtractedText", Text, nullable=True)
    chunks_json: Mapped[str | None] = mapped_column("ChunksJson", Text, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column("ExtractionMethod", String(64), nullable=True)
    extractor_version: Mapped[int] = mapped_column("ExtractorVersion", Integer, default=0)
    ocr_used: Mapped[bool] = mapped_column("OcrUsed", Boolean, default=False)
    truncated: Mapped[bool] = mapped_column("Truncated", Boolean, default=False)
    incomplete: Mapped[bool] = mapped_column("Incomplete", Boolean, default=False)
    attempt_count: Mapped[int] = mapped_column("AttemptCount", Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column("NextAttemptAt", DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column("LeaseOwner", String(256), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column("LeaseExpiresAt", DateTime(timezone=True), nullable=True)
    source_last_write_utc: Mapped[datetime | None] = mapped_column("SourceLastWriteUtc", DateTime(timezone=True), nullable=True)
    extraction_notes: Mapped[str | None] = mapped_column("ExtractionNotes", String(2000), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column("ContentSha256", String(64), nullable=True)
    safe_error: Mapped[str | None] = mapped_column("SafeError", String(1024), nullable=True)
    pilot_scoped: Mapped[bool] = mapped_column("PilotScoped", Boolean, default=False)
    non_confidential: Mapped[bool] = mapped_column("NonConfidential", Boolean, default=False)
    extracted_at: Mapped[datetime | None] = mapped_column("ExtractedAt", DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime(timezone=True))


class SynologyExtractionBatch(Base):
    __tablename__ = "synology_extraction_batch"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    action: Mapped[str] = mapped_column("Action", String(64), default="extract_missing")
    status: Mapped[int] = mapped_column("Status", Integer, default=0)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column("ActorUserId", Uuid(as_uuid=True), nullable=True)
    approved_share_id: Mapped[uuid.UUID | None] = mapped_column("ApprovedShareId", Uuid(as_uuid=True), nullable=True)
    sync_job_id: Mapped[uuid.UUID | None] = mapped_column("SyncJobId", Uuid(as_uuid=True), nullable=True, index=True)
    folder_prefix_normalized: Mapped[str] = mapped_column("FolderPrefixNormalized", String(2048), default="")
    force_outside_pilot: Mapped[bool] = mapped_column("ForceOutsidePilot", Boolean, default=False)
    queued_count: Mapped[int] = mapped_column("QueuedCount", Integer, default=0)
    processed_count: Mapped[int] = mapped_column("ProcessedCount", Integer, default=0)
    succeeded_count: Mapped[int] = mapped_column("SucceededCount", Integer, default=0)
    failed_count: Mapped[int] = mapped_column("FailedCount", Integer, default=0)
    skipped_count: Mapped[int] = mapped_column("SkippedCount", Integer, default=0)
    unsupported_count: Mapped[int] = mapped_column("UnsupportedCount", Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column("CancelRequested", Boolean, default=False)
    safe_error: Mapped[str | None] = mapped_column("SafeError", String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column("StartedAt", DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column("CompletedAt", DateTime(timezone=True), nullable=True)


class SynologyExtractionQueue(Base):
    __tablename__ = "synology_extraction_queue"

    id: Mapped[uuid.UUID] = mapped_column("Id", Uuid(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column("BatchId", Uuid(as_uuid=True), nullable=True)
    inventory_file_id: Mapped[uuid.UUID] = mapped_column("InventoryFileId", Uuid(as_uuid=True))
    approved_share_id: Mapped[uuid.UUID] = mapped_column("ApprovedShareId", Uuid(as_uuid=True))
    status: Mapped[int] = mapped_column("Status", Integer, default=0)
    force_outside_pilot: Mapped[bool] = mapped_column("ForceOutsidePilot", Boolean, default=False)
    attempt_count: Mapped[int] = mapped_column("AttemptCount", Integer, default=0)
    priority: Mapped[int] = mapped_column("Priority", Integer, default=100)
    lease_owner: Mapped[str | None] = mapped_column("LeaseOwner", String(256), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column("LeaseExpiresAt", DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column("NextAttemptAt", DateTime(timezone=True), nullable=True)
    safe_error: Mapped[str | None] = mapped_column("SafeError", String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime(timezone=True))


class AiConversation(Base):
    """Native OpenAI chat conversation owned by one QKA user (QKA-Next only)."""

    __tablename__ = "qka_next_ai_conversation"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    qka_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    title: Mapped[str] = mapped_column(String(256), default="New chat")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    messages: Mapped[list["AiMessage"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class AiMessage(Base):
    __tablename__ = "qka_next_ai_message"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("qka_next_ai_conversation.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="complete")
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    conversation: Mapped[AiConversation] = relationship(back_populates="messages")
    attachments: Mapped[list["AiMessageAttachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class AiMessageAttachment(Base):
    __tablename__ = "qka_next_ai_message_attachment"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("qka_next_ai_message.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    sample_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    display_name: Mapped[str] = mapped_column(String(512), default="")
    source_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    message: Mapped[AiMessage] = relationship(back_populates="attachments")


class AiSampleUpload(Base):
    """Admin-approved sample file for ACL-independent analysis (not Synology inventory)."""

    __tablename__ = "qka_next_ai_sample_upload"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    file_name: Mapped[str] = mapped_column(String(512))
    extension: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[int] = mapped_column(Integer, default=0)
    safe_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AiUsageAudit(Base):
    """Append-only OpenAI usage / error audit (no prompts, no document bodies, no API keys)."""

    __tablename__ = "qka_next_ai_usage_audit"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    qka_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    model: Mapped[str] = mapped_column(String(128), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)
    error_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdConnection(Base):
    """QKA-Next Active Directory connection.

    protocol/auth mode: windows_integrated (Kerberos/SSPI + signing/sealing) or ldaps.
    Bind password is used only for LDAPS and is encrypted at rest.
    """

    __tablename__ = "qka_next_ad_connection"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    connection_name: Mapped[str] = mapped_column(String(256), default="")
    domain_dns_name: Mapped[str] = mapped_column(String(256), default="")
    domain_controller: Mapped[str] = mapped_column(String(256), default="")
    protocol: Mapped[str] = mapped_column(String(32), default="windows_integrated")
    port: Mapped[int] = mapped_column(Integer, default=389)
    base_dn: Mapped[str] = mapped_column(String(1024), default="")
    bind_username: Mapped[str] = mapped_column(String(512), default="")
    bind_secret_protected: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=15)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    connection_status: Mapped[int] = mapped_column(Integer, default=0)  # 0 unknown 1 connected 2 failed 3 disabled
    last_successful_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_safe_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    last_diagnostics_json: Mapped[str] = mapped_column(Text, default="[]")
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_result: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_sync_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class AdOuScope(Base):
    """Selected OUs included in directory synchronization."""

    __tablename__ = "qka_next_ad_ou_scope"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    distinguished_name: Mapped[str] = mapped_column(String(2048))
    name: Mapped[str] = mapped_column(String(512), default="")
    selected: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdDirectoryUser(Base):
    """Discovered AD user (stable identity = objectGUID; SID for ACL)."""

    __tablename__ = "qka_next_ad_directory_user"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    object_guid: Mapped[str] = mapped_column(String(64), index=True)
    object_sid: Mapped[str] = mapped_column(String(256), default="")
    sam_account_name: Mapped[str] = mapped_column(String(256), default="")
    user_principal_name: Mapped[str] = mapped_column(String(512), default="")
    display_name: Mapped[str] = mapped_column(String(512), default="")
    mail: Mapped[str] = mapped_column(String(512), default="")
    department: Mapped[str] = mapped_column(String(512), default="")
    distinguished_name: Mapped[str] = mapped_column(String(2048), default="")
    account_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True)
    # Admin-controlled QKA enrollment eligibility (PostgreSQL only; never written to AD).
    qka_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    # Manual archive flag (PostgreSQL only). Disabled/missing accounts are treated as archived even when false.
    qka_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Department chosen at QKA enrollment when AD department is blank or overridden.
    department_override: Mapped[str] = mapped_column(String(512), default="")
    mapping_status: Mapped[int] = mapped_column(Integer, default=0)  # 0 unlinked 1 pending 2 linked 3 conflict
    linked_qka_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    linked_external_identity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    candidate_qka_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    candidate_google_email: Mapped[str] = mapped_column(String(512), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdDirectoryGroup(Base):
    """Discovered AD security group."""

    __tablename__ = "qka_next_ad_directory_group"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    object_guid: Mapped[str] = mapped_column(String(64), index=True)
    object_sid: Mapped[str] = mapped_column(String(256), default="")
    name: Mapped[str] = mapped_column(String(512), default="")
    distinguished_name: Mapped[str] = mapped_column(String(2048), default="")
    is_security_group: Mapped[bool] = mapped_column(Boolean, default=True)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdGroupMemberEdge(Base):
    """Bounded group membership edges (member may be user or nested group)."""

    __tablename__ = "qka_next_ad_group_member"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    group_object_guid: Mapped[str] = mapped_column(String(64), index=True)
    member_object_guid: Mapped[str] = mapped_column(String(64), default="")
    member_object_sid: Mapped[str] = mapped_column(String(256), default="")
    member_kind: Mapped[str] = mapped_column(String(16), default="user")  # user|group
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdSyncJob(Base):
    __tablename__ = "qka_next_ad_sync_job"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    status: Mapped[int] = mapped_column(Integer, default=0)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    users_discovered: Mapped[int] = mapped_column(Integer, default=0)
    users_processed: Mapped[int] = mapped_column(Integer, default=0)
    users_unchanged: Mapped[int] = mapped_column(Integer, default=0)
    users_updated: Mapped[int] = mapped_column(Integer, default=0)
    users_added: Mapped[int] = mapped_column(Integer, default=0)
    groups_discovered: Mapped[int] = mapped_column(Integer, default=0)
    linked_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)
    conflict_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    enumeration_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    safe_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AdAuditEvent(Base):
    __tablename__ = "qka_next_ad_audit"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    detail: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# --- Windows Desktop Agent (pilot) ---

DEVICE_PENDING = 0
DEVICE_ACTIVE = 1
DEVICE_PAUSED = 2
DEVICE_REVOKED = 3
DEVICE_ERROR = 4

INDEX_IDLE = 0
INDEX_RUNNING = 1
INDEX_PAUSED = 2
INDEX_ERROR = 3


# --- AD Workstation inventory (Sources → Employee Computers → Workstations) ---

REACH_UNCHECKED = 0
REACH_DNS_FAILED = 1
REACH_REACHABLE = 2
REACH_NO_REPLY = 3  # ICMP timeout/blocked — never treated as "powered off"
REACH_PROBE_ERROR = 4


class AdWorkstationOuScope(Base):
    """OUs selected for workstation computer sync (independent of user AD OU scope)."""

    __tablename__ = "qka_next_ad_workstation_ou_scope"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    distinguished_name: Mapped[str] = mapped_column(String(2048))
    name: Mapped[str] = mapped_column(String(512), default="")
    selected: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdWorkstation(Base):
    """Domain computer object synced from AD for Desktop Agent administration.

    Employee assignment is explicit (Super Admin); never inferred from computer name.
    """

    __tablename__ = "qka_next_ad_workstation"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    object_guid: Mapped[str] = mapped_column(String(64), index=True)
    object_sid: Mapped[str] = mapped_column(String(256), default="")
    computer_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    dns_hostname: Mapped[str] = mapped_column(String(512), default="")
    distinguished_name: Mapped[str] = mapped_column(String(2048), default="")
    ou_path: Mapped[str] = mapped_column(String(2048), default="", index=True)
    operating_system: Mapped[str] = mapped_column(String(512), default="")
    account_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True)
    is_server_or_dc: Mapped[bool] = mapped_column(Boolean, default=False)
    assigned_qka_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    desktop_device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    resolved_ip: Mapped[str] = mapped_column(String(64), default="")
    reachability_status: Mapped[int] = mapped_column(Integer, default=REACH_UNCHECKED)
    reachability_detail: Mapped[str] = mapped_column(String(1024), default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DesktopEnrollmentCode(Base):
    """One-time enrollment code bound to a QKA user. Never embedded in the MSI."""

    __tablename__ = "qka_next_desktop_enrollment_code"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    qka_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    code_hash: Mapped[str] = mapped_column(String(128), unique=True)
    label: Mapped[str] = mapped_column(String(256), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DesktopDevice(Base):
    __tablename__ = "qka_next_desktop_device"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    qka_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    device_key_hash: Mapped[str] = mapped_column(String(128), unique=True)
    computer_name: Mapped[str] = mapped_column(String(256), default="")
    device_fingerprint: Mapped[str] = mapped_column(String(256), default="")
    domain_joined_name: Mapped[str] = mapped_column(String(256), default="")
    agent_version: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[int] = mapped_column(Integer, default=DEVICE_PENDING)
    indexing_state: Mapped[int] = mapped_column(Integer, default=INDEX_IDLE)
    read_only_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    read_only_detail: Mapped[str] = mapped_column(String(1024), default="")
    last_error: Mapped[str] = mapped_column(String(2000), default="")
    files_total: Mapped[int] = mapped_column(Integer, default=0)
    files_indexed: Mapped[int] = mapped_column(Integer, default=0)
    files_pending: Mapped[int] = mapped_column(Integer, default=0)
    files_failed: Mapped[int] = mapped_column(Integer, default=0)
    files_by_type_json: Mapped[str] = mapped_column(Text, default="{}")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_index_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DesktopDeviceFolder(Base):
    __tablename__ = "qka_next_desktop_device_folder"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    path_normalized: Mapped[str] = mapped_column(String(2048))
    include_subfolders: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# Pending Google → QkaUser link (verified OAuth sub; Super Admin must approve).
# Never auto-merges by email. Status: 0=pending, 1=approved, 2=rejected.
PENDING_GOOGLE_LINK_PENDING = 0
PENDING_GOOGLE_LINK_APPROVED = 1
PENDING_GOOGLE_LINK_REJECTED = 2


class PendingGoogleLink(Base):
    """Verified Google OAuth identity awaiting Super Admin link to an existing QkaUser.

    Created when Google sign-in email matches an AD-enrolled user's contact email
    (or another explicit candidate) but Google is not yet linked. Login stays denied
    until approve; reject leaves identities separate. Never silently merges two
    active QKA users.
    """

    __tablename__ = "qka_next_pending_google_link"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    target_qka_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    google_sub: Mapped[str] = mapped_column(String(128), index=True)
    google_email_normalized: Mapped[str] = mapped_column(String(256), index=True)
    google_profile_name: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[int] = mapped_column(Integer, default=PENDING_GOOGLE_LINK_PENDING, index=True)
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    conflict_reason: Mapped[str] = mapped_column(String(1024), default="")
    conflicting_qka_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    external_identity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
