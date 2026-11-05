export type SessionUser = {
  id: string;
  displayName: string;
  superAdmin: boolean;
  directoryOperator: boolean;
  allowlist?: boolean;
};

export type AuthStatus = {
  googleConfigured: boolean;
  googleHostedDomain: string;
  devSessionEnabled: boolean;
  liveGoogleLoginVerified: boolean;
};

export type AllowedEmail = {
  id: string;
  email: string;
  enabled: boolean;
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type PendingGoogleLink = {
  id: string;
  targetQkaUserId: string;
  googleSub: string;
  googleEmail: string;
  googleProfileName?: string | null;
  status: "pending" | "approved" | "rejected";
  conflict: boolean;
  conflictReason?: string | null;
  conflictingQkaUserId?: string | null;
  createdAt?: string | null;
};

export type DirectoryUser = {
  allowlistId: string | null;
  qkaUserId: string | null;
  displayName: string;
  googleProfileName?: string | null;
  primaryEmail: string;
  contactEmail?: string | null;
  domainUsername?: string | null;
  department?: string;
  identitySource?: "ad" | "google" | "both" | "none";
  linkedIdentities?: "ad" | "google" | "both" | "pending";
  identityBadges?: Array<"ad" | "google" | "google_pending">;
  googleSub: string | null;
  googleLinked: boolean;
  adLinked?: boolean;
  googlePending?: boolean;
  pendingGoogleLink?: PendingGoogleLink | null;
  identityConflict?: boolean;
  identityConflictReason?: string | null;
  adBrowserLoginImplemented?: boolean;
  identityStatus: "linked" | "pending" | "disabled" | "ad_enrolled" | "eligible";
  accountStatus:
    | "active"
    | "disabled"
    | "pending"
    | "awaiting_google_login"
    | "awaiting_admin_approval"
    | "eligible_pending_enroll";
  canEnroll?: boolean;
  objectGuid?: string | null;
  objectSid?: string | null;
  adDirectoryUserId?: string | null;
  chatgptMembership: "verified_member" | "verified_nonmember" | "unknown";
  chatgptMembershipSource?: string;
  chatgptMembershipVerifiedAt?: string | null;
  chatgptMemberName?: string | null;
  localChatEnabled: boolean;
  mcpEnabled: boolean;
  allowlisted: boolean;
  allowlistEnabled: boolean;
  canGrant: boolean;
  adIdentities: Array<{
    domainUsername: string | null;
    sid?: string | null;
    sidSuffix: string | null;
    upn?: string | null;
    linked: boolean;
  }>;
  superAdmin: boolean;
  createdAt?: string | null;
  updatedAt?: string | null;
  adOnlyLoginImplemented?: boolean;
  created?: boolean;
  linkedExisting?: boolean;
};

export type SourceEmployee = DirectoryUser;

export type DesktopDevice = {
  id: string;
  qkaUserId: string;
  employeeName: string;
  computerName: string;
  domainJoinedName: string;
  deviceFingerprint: string;
  agentVersion: string;
  status: string;
  online: boolean;
  agentStatus: string;
  indexingState: string;
  readOnlyVerified: boolean;
  readOnlyDetail: string;
  lastError: string;
  filesTotal: number;
  filesIndexed: number;
  filesPending: number;
  filesFailed: number;
  filesByType: Record<string, number>;
  percentComplete: number;
  lastHeartbeatAt: string | null;
  lastIndexAt: string | null;
  enrolledAt: string | null;
  revokedAt: string | null;
  folders: Array<{ id: string; path: string; includeSubfolders: boolean; enabled: boolean }>;
  gpoDeployment?: { supported: boolean; status: string; note: string };
};

export type Workstation = {
  id: string;
  connectionId: string;
  computerName: string;
  dnsHostname: string;
  ipAddress: string;
  ou: string;
  operatingSystem: string;
  accountStatus: string;
  accountEnabled: boolean;
  isPresent: boolean;
  isServerOrDc: boolean;
  assignedEmployeeId: string | null;
  assignedEmployeeName: string;
  assignedEmployeeEmail: string;
  reachabilityStatus: string;
  reachabilityDetail: string;
  reachabilityNote: string;
  lastCheckedAt: string | null;
  lastSeenAt: string | null;
  agentStatus: string;
  agentStatusLabel: string;
  agentVersion: string;
  lastHeartbeatAt: string | null;
  indexingStatus: string;
  online: boolean;
  desktopDeviceId: string | null;
  objectSid?: string;
  objectGuid?: string;
  deployAgent: {
    enabled: boolean;
    status: string;
    note: string;
    prerequisites?: {
      httpsConfigured: boolean;
      gpoEngineAvailable: boolean;
      msiPresent: boolean;
      tlsTrust: string;
      machineIdentityEnrollmentReady: boolean;
    };
  };
  deploymentJob?: {
    id: string;
    status: string;
    statusCode: number;
    attemptCount: number;
    lastError: string;
    msiVersion: string;
    engine: string;
    enrollmentMode: string;
    desktopDeviceId: string | null;
    createdAt: string | null;
    updatedAt: string | null;
    approvedAt: string | null;
    scheduledAt: string | null;
    installedAt: string | null;
    cancelledAt: string | null;
  } | null;
};

export type DeployPrerequisites = {
  deployEnabled: boolean;
  httpsConfigured: boolean;
  httpsEndpoint: string;
  tlsTrust: string;
  msiPresent: boolean;
  msiPath: string;
  gpoEngineAvailable: boolean;
  gpoReason: string;
  gpoTargetGroup: string;
  machineIdentityEnrollmentReady: boolean;
  manualEnrollmentCodeFallback: boolean;
  blockers: string[];
  confirmationPhrase: string;
  maxBulk: number;
  realPcDeployAllowed: boolean;
  note: string;
};

export type WorkstationsPage = {
  items: Workstation[];
  total: number;
  page: number;
  pageSize: number;
  summary: {
    totalPresent: number;
    assigned: number;
    reachable: number;
    noReply: number;
    dnsFailed: number;
    unchecked: number;
  };
  ous: string[];
  scopeOnly?: boolean;
  activeSyncOus?: Array<{ name: string; distinguishedName: string }>;
};

export type DirectoryUsersPage = {
  items: DirectoryUser[];
  total: number;
  page: number;
  pageSize: number;
  membershipSync?: {
    mode: string;
    automaticSyncSupported: boolean;
    note: string;
    acceptedCsvColumns: string[];
  };
};

export type SourceScope = {
  id: string;
  approvedShareId: string;
  folderPrefixNormalized: string;
  trustClass: string;
  status: string;
  displayName: string;
  sourceOwnerName: string;
  sourceOwnerEmail: string;
  approvalReference: string;
  approvalNotes: string;
  approvedAt: string | null;
  approvedByUserId: string | null;
  serviceReadVerified: boolean;
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type SourceSource = {
  approvedShareId: string;
  shareName: string;
  inventoryCount: number;
  aclCapturedCount: number;
  pilotFolderPrefix: string;
  isConfiguredPilotShare: boolean;
  scopes: SourceScope[];
  accessModel?: "source_acl" | "qka_managed";
  eligibleForGrants?: boolean;
  authorizedForDelegation?: boolean;
};

export type SourceFolderNode = {
  id: string;
  label: string;
  path: string;
  isFile: boolean;
  lazy?: boolean;
  icon?: string;
  inventoryId?: string | null;
  aclCaptureStatus: number | null;
  children?: SourceFolderNode[];
};

export type SourceFolderPage = {
  parentPath: string;
  breadcrumb: Array<{ label: string; path: string }>;
  items: SourceFolderNode[];
  total: number;
  folderCount: number;
  fileCount: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
};

export type SourceGrant = {
  id: string;
  scopeId: string;
  qkaUserId: string | null;
  allowedEmailId?: string | null;
  pendingActivation?: boolean;
  capabilityView?: boolean;
  capabilityDownload?: boolean;
  capabilityRead?: boolean;
  capabilitySearch?: boolean;
  capabilityPreview?: boolean;
  pathMode: "all" | "selected";
  includeSubfolders: boolean;
  selectedPaths: string[];
  fileTypes: string[];
  status: string;
  reason: string;
  scope?: SourceScope | null;
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type SourceEffective = {
  qkaUserId: string;
  displayName: string;
  grants: SourceGrant[];
  sampleDocument: {
    documentId: string;
    fileName: string;
    relativePath: string;
    qkaGrant: string;
    sourcePermission: string;
    effectiveAccess: string;
    denialReason: string | null;
    allowed: boolean;
  } | null;
};

export type SourceAuditEntry = {
  id: string;
  action: string;
  actorUserId: string | null;
  changedBy: string;
  targetUserId: string | null;
  employeeEmail: string;
  shareName: string;
  scope: string;
  fileTypes: string[];
  scopeId: string | null;
  grantId: string | null;
  detail: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  createdAt: string | null;
};

export type SourceAuditPage = {
  items: SourceAuditEntry[];
  total: number;
  page: number;
  pageSize: number;
};

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type RequestOptions = RequestInit & {
  /** When true, a 401 does not trigger the global signed-out redirect (used by session probe). */
  allowUnauthorized?: boolean;
};

let unauthorizedHandler: (() => void) | null = null;
let unauthorizedHandling = false;

export function setUnauthorizedHandler(handler: (() => void) | null) {
  unauthorizedHandler = handler;
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|; )qka_next_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function isCredentialChallenge(path: string): boolean {
  return (
    path === "/api/v1/auth/login" ||
    path === "/api/v1/auth/dev-session" ||
    path.startsWith("/api/v1/auth/google/")
  );
}

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const { allowUnauthorized = false, ...fetchInit } = init ?? {};
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(fetchInit.headers as Record<string, string> | undefined),
  };
  if ((fetchInit.method ?? "GET").toUpperCase() !== "GET") {
    const csrf = csrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  const response = await fetch(path, {
    credentials: "include",
    ...fetchInit,
    headers,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    const message = typeof detail === "string" ? detail : JSON.stringify(detail);
    if (
      response.status === 401 &&
      !allowUnauthorized &&
      !isCredentialChallenge(path) &&
      unauthorizedHandler &&
      !unauthorizedHandling
    ) {
      unauthorizedHandling = true;
      try {
        unauthorizedHandler();
      } finally {
        queueMicrotask(() => {
          unauthorizedHandling = false;
        });
      }
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export type DocumentPreview = {
  id: string;
  denied: boolean;
  error?: string | null;
  title?: string;
  fileType?: string;
  extension?: string;
  relativePath?: string;
  folder?: string;
  sizeBytes?: number;
  previewKind?: string;
  previewAvailable?: boolean;
  previewNote?: string;
  canDownload?: boolean;
  viewMode?: string;
  dataView?: boolean;
  dataViewAvailable?: boolean;
  supportsOriginalFormat?: boolean;
  renderStatus?: string;
  engine?: string;
  pageUrl?: string;
  engineStatus?: {
    available?: boolean;
    path?: string | null;
    installHint?: string | null;
  };
  text?: string | null;
  hasMore?: boolean;
  sheets?: Array<{ index: number; name: string }>;
  sheet?: number;
  sheetName?: string;
  rows?: string[][];
  rowOffset?: number;
  rowCount?: number;
  blocks?: Array<{ type: string; text?: string; level?: number; rows?: string[][] }>;
  page?: number;
  pageCount?: number;
  slide?: number;
  slideCount?: number;
  body?: string;
  thumbnails?: Array<{ index: number; title: string }>;
  mediaAvailable?: boolean;
};

export const api = {
  health: () => request<{ status: string }>("/health"),
  authStatus: () => request<AuthStatus>("/api/v1/auth/status", { allowUnauthorized: true }),
  me: () => request<SessionUser>("/api/v1/auth/me", { allowUnauthorized: true }),
  login: (email: string) =>
    request<SessionUser>("/api/v1/auth/dev-session", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  passwordLogin: (username: string, password: string) =>
    request<SessionUser>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  startGoogle: () => {
    window.location.assign("/api/v1/auth/google/start");
  },
  listAllowedEmails: () => request<AllowedEmail[]>("/api/v1/access/emails"),
  addAllowedEmail: (email: string, displayName?: string) =>
    request<AllowedEmail>("/api/v1/access/emails", {
      method: "POST",
      body: JSON.stringify({ email, displayName: displayName || "" }),
    }),
  setAllowedEmailEnabled: (id: string, enabled: boolean) =>
    request<AllowedEmail>(`/api/v1/access/emails/${id}/enabled`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  removeAllowedEmail: (id: string) =>
    request<{ ok: boolean }>(`/api/v1/access/emails/${id}/remove`, { method: "POST" }),
  listDirectoryUsers: (params?: {
    search?: string;
    accountStatus?: string;
    identityStatus?: string;
    chatgptMembership?: string;
    sortBy?: string;
    sortDesc?: boolean;
    page?: number;
    pageSize?: number;
  }) => {
    const q = new URLSearchParams();
    if (params?.search) q.set("search", params.search);
    if (params?.accountStatus) q.set("accountStatus", params.accountStatus);
    if (params?.identityStatus) q.set("identityStatus", params.identityStatus);
    if (params?.chatgptMembership) q.set("chatgptMembership", params.chatgptMembership);
    if (params?.sortBy) q.set("sortBy", params.sortBy);
    if (params?.sortDesc != null) q.set("sortDesc", params.sortDesc ? "true" : "false");
    if (params?.page) q.set("page", String(params.page));
    if (params?.pageSize) q.set("pageSize", String(params.pageSize));
    const suffix = q.toString() ? `?${q.toString()}` : "";
    return request<DirectoryUsersPage>(`/api/v1/access/users${suffix}`);
  },
  listDepartments: () =>
    request<{ items: Array<{ value: string; label: string }>; total: number }>("/api/v1/access/departments"),
  createDirectoryUser: (body: {
    displayName?: string;
    email?: string;
    identitySource?: "google" | "active_directory" | "";
    objectGuid?: string;
    department?: string | null;
    departmentOverride?: string | null;
    contactEmail?: string | null;
    linkExistingQkaUserId?: string | null;
    localChatEnabled?: boolean;
    mcpEnabled?: boolean;
    enabled?: boolean;
  }) =>
    request<DirectoryUser>("/api/v1/access/users", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateDirectoryUser: (
    allowlistId: string,
    body: {
      displayName?: string;
      department?: string | null;
      enabled?: boolean;
      localChatEnabled?: boolean;
      mcpEnabled?: boolean;
    },
  ) =>
    request<DirectoryUser>(`/api/v1/access/users/${allowlistId}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateAdDirectoryUser: (
    qkaUserId: string,
    body: {
      displayName?: string;
      department?: string | null;
      contactEmail?: string | null;
      enabled?: boolean;
      localChatEnabled?: boolean;
      mcpEnabled?: boolean;
    },
  ) =>
    request<DirectoryUser>(`/api/v1/access/users/qka/${qkaUserId}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  approvePendingGoogleLink: (pendingId: string) =>
    request<{ ok: boolean; qkaUserId: string; user?: DirectoryUser; googleSub?: string }>(
      `/api/v1/access/identities/google-pending/${pendingId}/approve`,
      { method: "POST", body: "{}" },
    ),
  rejectPendingGoogleLink: (pendingId: string) =>
    request<{ ok: boolean; rejected?: boolean; user?: DirectoryUser }>(
      `/api/v1/access/identities/google-pending/${pendingId}/reject`,
      { method: "POST", body: "{}" },
    ),
  adEligibleUsers: (opts?: { search?: string; connectionId?: string; page?: number; pageSize?: number }) => {
    const q = new URLSearchParams();
    if (opts?.search) q.set("search", opts.search);
    if (opts?.connectionId) q.set("connectionId", opts.connectionId);
    if (opts?.page) q.set("page", String(opts.page));
    if (opts?.pageSize) q.set("pageSize", String(opts.pageSize));
    const suffix = q.toString() ? `?${q}` : "";
    return request<{
      items: Array<{
        id: string;
        objectGuid: string;
        objectSid: string;
        displayName: string;
        samAccountName: string;
        department: string;
        departmentBlank: boolean;
        mail: string | null;
        alreadyLinked: boolean;
        linkedQkaUserId: string | null;
        connectionId: string;
      }>;
      total: number;
      page: number;
      pageSize: number;
    }>(`/api/v1/admin/active-directory/eligible-users${suffix}`);
  },
  membershipSyncInfo: () =>
    request<{ mode: string; automaticSyncSupported: boolean; note: string; acceptedCsvColumns: string[] }>(
      "/api/v1/access/users/membership-sync",
    ),
  importMembershipCsv: async (file: File, markMissingAsNonmember = false, notes = "") => {
    const form = new FormData();
    form.append("file", file);
    form.append("markMissingAsNonmember", markMissingAsNonmember ? "true" : "false");
    form.append("notes", notes);
    const headers: Record<string, string> = {};
    const csrf = csrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;
    const response = await fetch("/api/v1/access/users/membership/import", {
      method: "POST",
      credentials: "include",
      headers,
      body: form,
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail ?? detail;
      } catch {
        /* ignore */
      }
      throw new ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return (await response.json()) as {
      batchId: string;
      mode: string;
      rowCount: number;
      matchedCount: number;
      createdCount: number;
      memberCount: number;
      nonmemberCount: number;
      note: string;
    };
  },
  sourceEmployees: () => request<SourceEmployee[]>("/api/v1/access/source/employees"),
  sourceSources: () => request<SourceSource[]>("/api/v1/access/source/sources"),
  desktopDevices: (qkaUserId?: string) => {
    const q = qkaUserId ? `?qkaUserId=${encodeURIComponent(qkaUserId)}` : "";
    return request<{ items: DesktopDevice[] }>(`/api/v1/admin/desktop-devices${q}`);
  },
  createDesktopEnrollmentCode: (body: { qkaUserId: string; label?: string }) =>
    request<{
      id: string;
      enrollmentCode: string;
      expiresAt: string;
      qkaUserId: string;
      serverBaseUrl: string;
      installHint: string;
    }>("/api/v1/admin/desktop-devices/enrollment-codes", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  setDesktopDeviceFolders: (deviceId: string, folders: Array<{ path: string; includeSubfolders?: boolean }>) =>
    request<{ folders: DesktopDevice["folders"] }>(`/api/v1/admin/desktop-devices/${deviceId}/folders`, {
      method: "PUT",
      body: JSON.stringify({ folders }),
    }),
  desktopDeviceCommand: (deviceId: string, command: "start" | "pause" | "resume") =>
    request<DesktopDevice>(`/api/v1/admin/desktop-devices/${deviceId}/command`, {
      method: "POST",
      body: JSON.stringify({ command }),
    }),
  revokeDesktopDevice: (deviceId: string) =>
    request<DesktopDevice>(`/api/v1/admin/desktop-devices/${deviceId}/revoke`, { method: "POST" }),
  workstations: (params?: {
    search?: string;
    ou?: string;
    accountStatus?: string;
    reachability?: string;
    agentStatus?: string;
    assigned?: string;
    includeMissing?: boolean;
    includeServers?: boolean;
    scopeOnly?: boolean;
    sort?: string;
    sortDir?: string;
    page?: number;
    pageSize?: number;
  }) => {
    const q = new URLSearchParams();
    if (params?.search) q.set("search", params.search);
    if (params?.ou) q.set("ou", params.ou);
    if (params?.accountStatus) q.set("accountStatus", params.accountStatus);
    if (params?.reachability) q.set("reachability", params.reachability);
    if (params?.agentStatus) q.set("agentStatus", params.agentStatus);
    if (params?.assigned) q.set("assigned", params.assigned);
    if (params?.includeMissing) q.set("includeMissing", "true");
    if (params?.includeServers) q.set("includeServers", "true");
    if (params?.scopeOnly === false) q.set("scopeOnly", "false");
    if (params?.scopeOnly === true) q.set("scopeOnly", "true");
    if (params?.sort) q.set("sort", params.sort);
    if (params?.sortDir) q.set("sortDir", params.sortDir);
    if (params?.page) q.set("page", String(params.page));
    if (params?.pageSize) q.set("pageSize", String(params.pageSize));
    const qs = q.toString();
    return request<WorkstationsPage>(`/api/v1/admin/workstations${qs ? `?${qs}` : ""}`);
  },
  workstationSyncOuOptions: (opts?: { connectionId?: string; parentDn?: string; search?: string }) => {
    const q = new URLSearchParams();
    if (opts?.connectionId) q.set("connectionId", opts.connectionId);
    if (opts?.parentDn) q.set("parentDn", opts.parentDn);
    if (opts?.search) q.set("search", opts.search);
    const qs = q.toString();
    return request<{
      connectionId: string;
      connectionName: string;
      baseDn: string;
      parentDn: string;
      includesChildOus: boolean;
      note: string;
      selectedItems: Array<{ name: string; distinguishedName: string; selected: boolean }>;
      items: Array<{ name: string; distinguishedName: string; selected: boolean }>;
    }>(`/api/v1/admin/workstations/sync-ou-options${qs ? `?${qs}` : ""}`);
  },
  saveWorkstationOuScope: (body: {
    connectionId?: string;
    items: Array<{ name: string; distinguishedName: string; selected: boolean }>;
  }) =>
    request<{
      connectionId: string;
      selectedCount: number;
      items: Array<{ id: string; name: string; distinguishedName: string; selected: boolean }>;
    }>("/api/v1/admin/workstations/ou-scope", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  syncWorkstations: (body: {
    connectionId?: string;
    ouDns: string[];
    includeServers?: boolean;
    persistOuScope?: boolean;
  }) =>
    request<{
      connectionId: string;
      discovered: number;
      created: number;
      updated: number;
      missingMarked: number;
      total: number;
      present: number;
      includeServers: boolean;
      syncedOus: string[];
      syncedAt: string;
    }>("/api/v1/admin/workstations/sync", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  pingWorkstation: (workstationId: string, timeoutMs = 2000) =>
    request<Workstation>(`/api/v1/admin/workstations/${workstationId}/ping`, {
      method: "POST",
      body: JSON.stringify({ timeoutMs }),
    }),
  pingWorkstationsBulk: (workstationIds: string[], opts?: { timeoutMs?: number; concurrency?: number }) =>
    request<{
      processed: number;
      concurrency: number;
      timeoutMs: number;
      results: Array<{
        id: string;
        computerName: string;
        reachabilityStatus: string;
        ipAddress: string;
        detail: string;
      }>;
      items: Workstation[];
      note: string;
    }>("/api/v1/admin/workstations/ping-bulk", {
      method: "POST",
      body: JSON.stringify({
        workstationIds,
        timeoutMs: opts?.timeoutMs ?? 2000,
        concurrency: opts?.concurrency ?? 8,
      }),
    }),
  assignWorkstationEmployee: (workstationId: string, qkaUserId: string | null) =>
    request<Workstation>(`/api/v1/admin/workstations/${workstationId}/employee`, {
      method: "PUT",
      body: JSON.stringify({ qkaUserId }),
    }),
  workstationDeployPrerequisites: () =>
    request<DeployPrerequisites>("/api/v1/admin/workstations/deploy/prerequisites"),
  deployWorkstationAgents: (body: {
    workstationIds: string[];
    confirmation: string;
    approve?: boolean;
  }) =>
    request<{
      ok: boolean;
      deployEnabled: boolean;
      prerequisites: DeployPrerequisites;
      jobs: Array<{
        id: string;
        workstationId: string;
        computerName: string;
        status: string;
        lastError: string;
        machineEnrollmentToken?: string | null;
      }>;
      tokensIssued: number;
      gpoManifest: Record<string, unknown> | null;
      realPcDeployExecuted: boolean;
      note: string;
    }>("/api/v1/admin/workstations/deploy", {
      method: "POST",
      body: JSON.stringify({ approve: true, ...body }),
    }),
  cancelWorkstationDeploy: (jobId: string) =>
    request<{ id: string; status: string }>(`/api/v1/admin/workstations/deploy/${jobId}/cancel`, {
      method: "POST",
      body: "{}",
    }),
  retryWorkstationDeploy: (jobId: string) =>
    request<{ id: string; status: string; attemptCount: number }>(
      `/api/v1/admin/workstations/deploy/${jobId}/retry`,
      { method: "POST", body: "{}" },
    ),
  sourceFolderTree: (approvedShareId: string, rootPrefix?: string | null) => {
    const q = new URLSearchParams({ approvedShareId });
    // "" / omitted → entire share; otherwise scoped prefix
    if (rootPrefix !== undefined && rootPrefix !== null) q.set("rootPrefix", rootPrefix);
    return request<SourceFolderNode[]>(`/api/v1/access/source/folder-tree?${q.toString()}`);
  },
  sourceFolderChildren: (
    approvedShareId: string,
    parentPath = "",
    params?: { search?: string; fileType?: string; page?: number; pageSize?: number },
  ) => {
    const q = new URLSearchParams({ approvedShareId, parentPath });
    if (params?.search) q.set("search", params.search);
    if (params?.fileType) q.set("fileType", params.fileType);
    if (params?.page) q.set("page", String(params.page));
    if (params?.pageSize) q.set("pageSize", String(params.pageSize));
    return request<SourceFolderPage>(`/api/v1/access/source/folder-children?${q.toString()}`);
  },
  sourceFolderSearch: (approvedShareId: string, query: string, parentPath = "") => {
    const q = new URLSearchParams({ approvedShareId, q: query, parentPath });
    return request<{ items: SourceFolderNode[]; total: number }>(`/api/v1/access/source/folder-search?${q.toString()}`);
  },
  sourceScopes: () => request<SourceScope[]>("/api/v1/access/source/scopes"),
  sourceEligibleScopes: (approvedShareId: string) =>
    request<{ eligible: boolean; accessModel?: string; scopes: SourceScope[]; reason: string | null }>(
      `/api/v1/access/source/eligible-scopes?approvedShareId=${encodeURIComponent(approvedShareId)}`,
    ),
  registerCorporateScope: (body: {
    approvedShareId: string;
    folderPrefix: string;
    displayName?: string;
    sourceOwnerName: string;
    sourceOwnerEmail: string;
    approvalReference: string;
    approvalNotes?: string;
    serviceReadVerified: boolean;
  }) =>
    request<SourceScope>("/api/v1/access/source/scopes/corporate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  revokeSourceScope: (scopeId: string) =>
    request<SourceScope>(`/api/v1/access/source/scopes/${scopeId}/revoke`, { method: "POST" }),
  setChatAccess: (body: { allowlistId: string; localChatEnabled: boolean; mcpEnabled: boolean }) =>
    request<{ allowlistId: string; email: string; localChatEnabled: boolean; mcpEnabled: boolean }>(
      "/api/v1/access/source/chat-access",
      { method: "POST", body: JSON.stringify(body) },
    ),
  sourceGrants: (params?: { scopeId?: string; qkaUserId?: string }) => {
    const q = new URLSearchParams();
    if (params?.scopeId) q.set("scopeId", params.scopeId);
    if (params?.qkaUserId) q.set("qkaUserId", params.qkaUserId);
    const suffix = q.toString() ? `?${q.toString()}` : "";
    return request<SourceGrant[]>(`/api/v1/access/source/grants${suffix}`);
  },
  upsertSourceGrant: (body: {
    scopeId?: string | null;
    approvedShareId?: string | null;
    qkaUserId?: string | null;
    allowlistId?: string | null;
    capabilityView?: boolean;
    capabilityDownload?: boolean;
    capabilityRead?: boolean;
    capabilitySearch?: boolean;
    capabilityPreview?: boolean;
    reason?: string;
    pathMode?: "all" | "selected";
    includeSubfolders?: boolean;
    selectedPaths?: string[];
    fileTypes?: string[];
  }) =>
    request<SourceGrant>("/api/v1/access/source/grants", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  setSourceGrantStatus: (grantId: string, status: "active" | "disabled" | "revoked", reason?: string) =>
    request<SourceGrant>(`/api/v1/access/source/grants/${grantId}/status`, {
      method: "POST",
      body: JSON.stringify({ status, reason: reason || "" }),
    }),
  sourceEffective: (userId: string, documentId?: string) => {
    const q = documentId ? `?documentId=${encodeURIComponent(documentId)}` : "";
    return request<SourceEffective>(`/api/v1/access/source/effective/${userId}${q}`);
  },
  sourceAudit: (params?: {
    search?: string;
    employeeEmail?: string;
    shareName?: string;
    action?: string;
    page?: number;
    pageSize?: number;
  }) => {
    const q = new URLSearchParams();
    if (params?.search) q.set("search", params.search);
    if (params?.employeeEmail) q.set("employeeEmail", params.employeeEmail);
    if (params?.shareName) q.set("shareName", params.shareName);
    if (params?.action) q.set("action", params.action);
    q.set("page", String(params?.page ?? 1));
    q.set("pageSize", String(params?.pageSize ?? 20));
    return request<SourceAuditPage>(`/api/v1/access/source/audit?${q.toString()}`);
  },
  logout: () => request<{ ok: boolean }>("/api/v1/auth/logout", { method: "POST", allowUnauthorized: true }),
  summary: () =>
    request<{
      inventoryCount: number;
      extractedCount: number;
      googleWorkspaceUsers: number;
      allowlistedEmails: number;
      synologyConnections: number;
      sourceCount: number;
      sources: Array<{ id: string; name: string; kind: string; connectionCount: number }>;
      openaiConfigured: boolean;
      mcpParity: boolean;
      oauthParity: boolean;
    }>("/api/v1/diagnostics/summary"),
  chatgptStatus: () => request<IntegrationStatus>("/api/v1/admin/integrations/chatgpt/status"),
  chatgptDiagnostics: () => request<Record<string, unknown>>("/api/v1/admin/integrations/chatgpt/diagnostics"),
  chatgptTest: () =>
    request<{
      checkedAt: string;
      overallOk: boolean;
      steps: Array<{ name: string; ok: boolean; detail: string | null }>;
      chatgptLiveVerified: boolean;
      note: string;
    }>("/api/v1/admin/integrations/chatgpt/test", { method: "POST" }),
  adHubStatus: () =>
    request<{
      synology: { status: string; lastSuccessfulCheck?: string | null };
      activeDirectory: { status: string; lastSuccessfulCheck?: string | null };
      googleWorkspace: { status: string; note?: string };
      chatgptMcp: { status: string; note?: string };
    }>("/api/v1/admin/active-directory/hub-status"),
  adConnections: () => request<AdConnection[]>("/api/v1/admin/active-directory/connections"),
  adOverview: (connectionId?: string) => {
    const q = connectionId ? `?connectionId=${encodeURIComponent(connectionId)}` : "";
    return request<AdOverview>(`/api/v1/admin/active-directory/overview${q}`);
  },
  adTestConnection: (body: AdConnectionBody, connectionId?: string) => {
    const q = connectionId ? `?connectionId=${encodeURIComponent(connectionId)}` : "";
    return request<AdConnectionTestResult>(`/api/v1/admin/active-directory/connections/test${q}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  adCreateConnection: (body: AdConnectionBody) =>
    request<AdConnection>("/api/v1/admin/active-directory/connections", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  adUpdateConnection: (connectionId: string, body: AdConnectionBody) =>
    request<AdConnection>(`/api/v1/admin/active-directory/connections/${connectionId}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  adDisableConnection: (connectionId: string) =>
    request<AdConnection>(`/api/v1/admin/active-directory/connections/${connectionId}/disable`, {
      method: "POST",
    }),
  adEnableConnection: (connectionId: string) =>
    request<AdConnection>(`/api/v1/admin/active-directory/connections/${connectionId}/enable`, {
      method: "POST",
    }),
  adBrowseOus: (connectionId: string, opts?: { parentDn?: string; search?: string }) => {
    const q = new URLSearchParams();
    if (opts?.parentDn) q.set("parentDn", opts.parentDn);
    if (opts?.search) q.set("search", opts.search);
    const suffix = q.toString() ? `?${q}` : "";
    return request<{
      parentDn: string;
      items: Array<{ name: string; distinguishedName: string; selected: boolean }>;
    }>(`/api/v1/admin/active-directory/connections/${connectionId}/ous${suffix}`);
  },
  adOuScope: (connectionId: string) =>
    request<{ items: Array<{ id: string; name: string; distinguishedName: string; selected: boolean }> }>(
      `/api/v1/admin/active-directory/connections/${connectionId}/ou-scope`,
    ),
  adSaveOuScope: (
    connectionId: string,
    items: Array<{ name: string; distinguishedName: string; selected: boolean }>,
  ) =>
    request<{ items: Array<{ id: string; name: string; distinguishedName: string; selected: boolean }> }>(
      `/api/v1/admin/active-directory/connections/${connectionId}/ou-scope`,
      { method: "PUT", body: JSON.stringify({ items }) },
    ),
  adDepartments: (connectionId: string) =>
    request<{ items: Array<{ value: string; label: string }>; total: number }>(
      `/api/v1/admin/active-directory/connections/${connectionId}/departments`,
    ),
  adUsers: (
    connectionId: string,
    opts?: {
      search?: string;
      mappingStatus?: number;
      accountStatus?: string;
      eligibility?: string;
      department?: string;
      archived?: boolean;
      sortBy?: string;
      sortDesc?: boolean;
      page?: number;
      pageSize?: number;
    },
  ) => {
    const q = new URLSearchParams();
    if (opts?.search) q.set("search", opts.search);
    if (opts?.mappingStatus != null) q.set("mappingStatus", String(opts.mappingStatus));
    if (opts?.accountStatus) q.set("accountStatus", opts.accountStatus);
    if (opts?.eligibility) q.set("eligibility", opts.eligibility);
    if (opts?.department) q.set("department", opts.department);
    if (opts?.archived) q.set("archived", "true");
    q.set("sortBy", opts?.sortBy || "displayName");
    q.set("sortDesc", opts?.sortDesc ? "true" : "false");
    if (opts?.page) q.set("page", String(opts.page));
    if (opts?.pageSize) q.set("pageSize", String(opts.pageSize));
    const suffix = q.toString() ? `?${q}` : "";
    return request<{ items: any[]; total: number; page: number; pageSize: number; sortBy?: string; sortDesc?: boolean; archived?: boolean }>(
      `/api/v1/admin/active-directory/connections/${connectionId}/users${suffix}`,
    );
  },
  adSetEligibility: (connectionId: string, directoryUserIds: string[], eligible: boolean) =>
    request<{ ok: boolean; updated: number; skipped: any[]; errors: any[]; eligible: boolean }>(
      `/api/v1/admin/active-directory/connections/${connectionId}/eligibility`,
      { method: "POST", body: JSON.stringify({ directoryUserIds, eligible }) },
    ),
  adSetArchived: (connectionId: string, directoryUserIds: string[], archived: boolean) =>
    request<{ ok: boolean; updated: number; skipped: any[]; errors: any[]; archived: boolean }>(
      `/api/v1/admin/active-directory/connections/${connectionId}/archive`,
      { method: "POST", body: JSON.stringify({ directoryUserIds, archived }) },
    ),
  adGroups: (connectionId: string, opts?: { search?: string }) => {
    const q = new URLSearchParams();
    if (opts?.search) q.set("search", opts.search);
    const suffix = q.toString() ? `?${q}` : "";
    return request<{ items: any[]; total: number }>(
      `/api/v1/admin/active-directory/connections/${connectionId}/groups${suffix}`,
    );
  },
  adApproveLink: (connectionId: string, userId: string, qkaUserId?: string) =>
    request<any>(`/api/v1/admin/active-directory/connections/${connectionId}/users/${userId}/link`, {
      method: "POST",
      body: JSON.stringify({ qkaUserId: qkaUserId || null }),
    }),
  adUnlink: (connectionId: string, userId: string) =>
    request<{ ok: boolean }>(
      `/api/v1/admin/active-directory/connections/${connectionId}/users/${userId}/unlink`,
      { method: "POST" },
    ),
  adSync: (connectionId: string) =>
    request<any>(`/api/v1/admin/active-directory/connections/${connectionId}/sync`, { method: "POST" }),
  adResync: (connectionId: string) =>
    request<any>(`/api/v1/admin/active-directory/connections/${connectionId}/resync`, { method: "POST" }),
  adSyncHistory: (connectionId: string) =>
    request<any[]>(`/api/v1/admin/active-directory/connections/${connectionId}/sync/history`),
  adAudit: (connectionId?: string) => {
    const q = connectionId ? `?connectionId=${encodeURIComponent(connectionId)}` : "";
    return request<any[]>(`/api/v1/admin/active-directory/audit${q}`);
  },
  searchCatalog: () =>
    request<{
      sources: Array<{
        approvedShareId: string;
        shareName: string;
        totalFiles: number;
        types: Array<{ key: string; label: string; icon: string; count: number }>;
      }>;
      totalFiles: number;
      error: string | null;
    }>("/api/v1/search/catalog"),
  search: (opts: {
    q?: string;
    fileType?: string | null;
    page?: number;
    pageSize?: number;
    sort?: string;
    signal?: AbortSignal;
  }) => {
    const params = new URLSearchParams();
    if (opts.q) params.set("q", opts.q);
    if (opts.fileType) params.set("fileType", opts.fileType);
    params.set("page", String(opts.page ?? 1));
    params.set("pageSize", String(opts.pageSize ?? 50));
    if (opts.sort) params.set("sort", opts.sort);
    return request<{
      results: Array<{
        id: string;
        title: string;
        fileName?: string;
        url: string;
        snippet: string;
        relativePath: string;
        folder?: string;
        extension: string;
        fileType: string;
        sizeBytes: number;
        modifiedAt?: string | null;
        source?: string;
        matchType?: string;
        previewAvailability?: string;
        canDownload?: boolean;
        openWithLabel?: string | null;
        recommendedApp?: string | null;
      }>;
      total: number;
      page: number;
      pageSize: number;
      fileType: string | null;
      query: string;
      error: string | null;
    }>(`/api/v1/search?${params.toString()}`, { signal: opts.signal });
  },
  documents: (q = "") =>
    request<{
      items: Array<{
        id: string;
        fileName: string;
        relativePath: string;
        extension: string;
        fileType: string;
        sizeBytes: number;
      }>;
      total: number;
      error: string | null;
    }>(`/api/v1/documents?q=${encodeURIComponent(q)}`),
  document: (id: string, offset = 0) =>
    request<{
      id: string;
      title?: string;
      text?: string;
      denied: boolean;
      error?: string | null;
      hasMore?: boolean;
      totalChars?: number;
      offset?: number;
      fileType?: string;
      relativePath?: string;
    }>(`/api/v1/documents/${id}?offset=${offset}`),
  documentPreview: (
    id: string,
    opts?: {
      sheet?: number;
      page?: number;
      slide?: number;
      rowOffset?: number;
      view?: "original" | "data";
      signal?: AbortSignal;
    },
  ) => {
    const params = new URLSearchParams();
    if (opts?.sheet != null) params.set("sheet", String(opts.sheet));
    if (opts?.page != null) params.set("page", String(opts.page));
    if (opts?.slide != null) params.set("slide", String(opts.slide));
    if (opts?.rowOffset != null) params.set("rowOffset", String(opts.rowOffset));
    if (opts?.view) params.set("view", opts.view);
    const qs = params.toString();
    return request<DocumentPreview>(`/api/v1/documents/${id}/preview${qs ? `?${qs}` : ""}`, {
      signal: opts?.signal,
    });
  },
  documentPreviewMedia: (id: string) => `/api/v1/documents/${id}/preview/media`,
  documentPreviewPage: (id: string, page: number) => `/api/v1/documents/${id}/preview/pages/${page}`,
  mcpHandoffMeta: (handoffId: string) =>
    request<{
      denied: boolean;
      purpose?: string;
      documentId?: string;
      documentName?: string;
      previewKind?: string;
      error?: string | null;
    }>(`/api/v1/mcp/handoff/${encodeURIComponent(handoffId)}`),
  createMcpIndexingConfirmation: (body: {
    operation: string;
    sourceId?: string | null;
    documentIds?: string[];
  }) =>
    request<{
      denied?: boolean;
      confirmationId: string;
      operation: string;
      sourceId?: string | null;
      documentIds?: string[];
      expiresAt: string;
      note?: string;
      error?: string;
    }>("/api/v1/admin/synology/mcp-indexing-confirmations", {
      method: "POST",
      body: JSON.stringify({
        operation: body.operation,
        sourceId: body.sourceId || null,
        documentIds: body.documentIds || [],
      }),
    }),
  documentDownloadMeta: (id: string) =>
    request<{
      id: string;
      denied: boolean;
      error?: string | null;
      fileName?: string;
      requiresDownloadGrant?: boolean;
      recommendedApp?: string;
      openWithLabel?: string;
    }>(`/api/v1/documents/${id}/download?meta=1`),
  documentDesktopOpen: (id: string) =>
    request<{
      ok: boolean;
      denied?: boolean;
      error?: string | null;
      fileName?: string;
      recommendedApp?: string;
      openWithLabel?: string;
      openUrl?: string | null;
      protocolUrl?: string | null;
      mode?: "local-launch" | "office-protocol" | "browser";
      expiresInSeconds?: number | null;
    }>(`/api/v1/documents/${id}/desktop-open`, { method: "POST", body: "{}" }),
  documentDownload: async (id: string, opts?: { intent?: "download" | "open" }) => {
    const intent = opts?.intent || "download";
    const response = await fetch(`/api/v1/documents/${id}/download?intent=${encodeURIComponent(intent)}`, {
      credentials: "include",
    });
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      const body = await response.json();
      return {
        ok: false as const,
        denied: Boolean(body.denied),
        error: body.error || "Download denied.",
        fileName: body.fileName as string | undefined,
        recommendedApp: body.recommendedApp as string | undefined,
        openWithLabel: body.openWithLabel as string | undefined,
      };
    }
    if (!response.ok) {
      return { ok: false as const, denied: true, error: response.statusText };
    }
    const blob = await response.blob();
    const disposition = response.headers.get("content-disposition") || "";
    const match = /filename\*=UTF-8''([^;]+)|filename=\"([^\"]+)\"/i.exec(disposition);
    const fileName = match ? decodeURIComponent(match[1] || match[2]) : "document";
    const recommendedApp = response.headers.get("x-qka-recommended-app") || undefined;
    const openWithLabel = response.headers.get("x-qka-open-with-label") || undefined;
    return { ok: true as const, blob, fileName, recommendedApp, openWithLabel };
  },
  aiStatus: () => request<AiStatus>("/api/v1/ai/status"),
  aiConversations: () => request<{ items: AiConversationSummary[] }>("/api/v1/ai/conversations"),
  aiConversation: (id: string) => request<AiConversationDetail>(`/api/v1/ai/conversations/${id}`),
  aiCreateConversation: (title?: string) =>
    request<AiConversationSummary>("/api/v1/ai/conversations", {
      method: "POST",
      body: JSON.stringify({ title: title ?? null }),
    }),
  aiDeleteConversation: (id: string) =>
    request<{ ok: boolean }>(`/api/v1/ai/conversations/${id}`, { method: "DELETE" }),
  aiSampleUpload: async (file: File) => {
    const headers: Record<string, string> = {};
    const csrf = csrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;
    const form = new FormData();
    form.append("file", file);
    const response = await fetch("/api/v1/ai/sample-upload", {
      method: "POST",
      credentials: "include",
      headers,
      body: form,
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail ?? detail;
      } catch {
        /* ignore */
      }
      throw new ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return (await response.json()) as AiSampleUploadResult;
  },
  aiChatStream: async (
    body: {
      message: string;
      conversationId?: string | null;
      documentIds?: string[];
      sampleIds?: string[];
    },
    handlers: {
      signal?: AbortSignal;
      onMeta?: (meta: AiStreamMeta) => void;
      onDelta?: (text: string) => void;
      onDone?: (payload: AiStreamDone) => void;
      onError?: (err: { code?: string; message: string; partial?: string }) => void;
    },
  ) => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const csrf = csrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;
    const response = await fetch("/api/v1/ai/chat/stream", {
      method: "POST",
      credentials: "include",
      headers,
      signal: handlers.signal,
      body: JSON.stringify({
        message: body.message,
        conversationId: body.conversationId ?? null,
        documentIds: body.documentIds ?? [],
        sampleIds: body.sampleIds ?? [],
      }),
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const errBody = await response.json();
        detail = errBody.detail ?? detail;
      } catch {
        /* ignore */
      }
      throw new ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    if (!response.body) {
      throw new ApiError(500, "Streaming response body missing.");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let currentEvent = "message";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n");
      buffer = parts.pop() ?? "";
      for (const raw of parts) {
        const line = raw.replace(/\r$/, "");
        if (!line) {
          currentEvent = "message";
          continue;
        }
        if (line.startsWith("event:")) {
          currentEvent = line.slice(6).trim();
          continue;
        }
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        let data: Record<string, unknown>;
        try {
          data = JSON.parse(payload) as Record<string, unknown>;
        } catch {
          continue;
        }
        if (currentEvent === "meta") handlers.onMeta?.(data as unknown as AiStreamMeta);
        else if (currentEvent === "delta") handlers.onDelta?.(String(data.text ?? ""));
        else if (currentEvent === "done") handlers.onDone?.(data as unknown as AiStreamDone);
        else if (currentEvent === "error") {
          handlers.onError?.({
            code: typeof data.code === "string" ? data.code : undefined,
            message: String(data.message ?? "AI error"),
            partial: typeof data.partial === "string" ? data.partial : undefined,
          });
        }
      }
    }
  },
  synologyConnections: () => request<SynologyConnection[]>("/api/v1/admin/synology/connections"),
  synologyConnection: (connectionId?: string) =>
    request<SynologyConnection>(
      connectionId
        ? `/api/v1/admin/synology/connections/${connectionId}`
        : "/api/v1/admin/synology/connection",
    ),
  createSynologyConnection: (body: {
    name: string;
    sharedFolderPath: string;
    authMode: number;
    serviceAccountUserName?: string;
    serviceAccountSecret?: string;
  }) =>
    request<SynologyConnection>("/api/v1/admin/synology/connections", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  saveSynologyConnection: (
    body: {
      name?: string;
      sharedFolderPath: string;
      authMode: number;
      serviceAccountUserName?: string;
      serviceAccountSecret?: string;
    },
    connectionId?: string,
  ) =>
    request<SynologyConnection>(
      connectionId ? `/api/v1/admin/synology/connections/${connectionId}` : "/api/v1/admin/synology/connection",
      {
        method: "PUT",
        body: JSON.stringify(body),
      },
    ),
  removeSynologyConnection: (connectionId: string) =>
    request<{ ok: boolean }>(`/api/v1/admin/synology/connections/${connectionId}/remove`, { method: "POST" }),
  testSynologyConnection: (connectionId?: string) =>
    request<SynologyTestResult>(
      connectionId
        ? `/api/v1/admin/synology/connections/${connectionId}/test`
        : "/api/v1/admin/synology/connection/test",
      { method: "POST" },
    ),
  synologyMonitor: (connectionId?: string) => {
    const query = connectionId ? `?connectionId=${encodeURIComponent(connectionId)}` : "";
    return request<SynologyMonitor>(`/api/v1/admin/synology/sync/monitor${query}`);
  },
  startSynologySync: (connectionId?: string) =>
    request<SynologyJob>("/api/v1/admin/synology/sync/start", {
      method: "POST",
      body: JSON.stringify({ connectionId: connectionId || null }),
    }),
  cancelSynologyJob: (jobId: string) =>
    request<{ ok: boolean }>(`/api/v1/admin/synology/sync/jobs/${jobId}/cancel`, { method: "POST" }),
  synologyInventory: (params: {
    search?: string;
    fileType?: number;
    page: number;
    pageSize: number;
    connectionId?: string;
  }) => {
    const query = new URLSearchParams();
    if (params.search) query.set("search", params.search);
    if (params.fileType != null) query.set("fileType", String(params.fileType));
    if (params.connectionId) query.set("connectionId", params.connectionId);
    query.set("page", String(params.page));
    query.set("pageSize", String(params.pageSize));
    return request<SynologyInventoryPage>(`/api/v1/admin/synology/inventory?${query.toString()}`);
  },
  synologyExtractionStatus: (connectionId?: string) => {
    const query = connectionId ? `?connectionId=${encodeURIComponent(connectionId)}` : "";
    return request<ExtractionStatus>(`/api/v1/admin/synology/extraction/status${query}`);
  },
  synologyExtractMissing: (body: { connectionId?: string; folderPrefix?: string; forceOutsidePilot?: boolean }) =>
    request<ExtractionBatch>("/api/v1/admin/synology/extraction/extract-missing", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  synologyRetryFailed: (body: { connectionId?: string }) =>
    request<ExtractionBatch>("/api/v1/admin/synology/extraction/retry-failed", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  synologyReindexSelected: (body: { inventoryIds: string[]; forceOutsidePilot?: boolean }) =>
    request<ExtractionBatch>("/api/v1/admin/synology/extraction/reindex-selected", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  synologyReindexFolder: (body: { connectionId?: string; folderPrefix: string; forceOutsidePilot?: boolean }) =>
    request<ExtractionBatch>("/api/v1/admin/synology/extraction/reindex-folder", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

export type SynologyConnection = {
  id: string;
  name: string;
  hostName: string;
  configuredSharedFolderPath: string;
  primaryApprovedShareId?: string | null;
  authMode: number;
  serviceAccountUserName: string | null;
  serviceAccountSecretConfigured: boolean;
  connectionStatus: number;
  port445Reachable: boolean | null;
  authenticatedShareAccess: boolean | null;
  lastSuccessfulTestAt: string | null;
  lastSafeError: string | null;
};

export type SynologyTestResult = {
  succeeded: boolean;
  status: number;
  port445Reachable: boolean;
  authenticatedShareAccess: boolean;
  safeError: string | null;
  diagnostics: string[];
};

export type SynologyJobIndexing = {
  batchId: string | null;
  provisional: boolean;
  queued: number;
  processing: number;
  processed: number;
  indexed: number;
  failed: number;
  skipped: number;
  unsupported: number;
  unchanged: number;
  percentComplete: number | null;
  status: number | null;
};

export type SynologyJob = {
  id: string;
  trigger: number;
  status: number;
  runStage?: string;
  currentOperation: string | null;
  enumerationComplete?: boolean;
  filesDiscovered: number;
  filesProcessed: number;
  filesNew: number;
  filesModified: number;
  filesUnchanged: number;
  filesRemoved: number;
  filesFailed?: number;
  discoveryPercentComplete?: number | null;
  percentComplete: number | null;
  indexing?: SynologyJobIndexing;
  completedWithErrors?: boolean;
  extractionBatchId?: string | null;
  safeError: string | null;
};

export type SynologyMonitor = {
  connection: SynologyConnection;
  connections?: SynologyConnection[];
  currentJob: SynologyJob | null;
  lastSuccessfulSyncAt: string | null;
  stats: {
    excel: number;
    pdf: number;
    word: number;
    powerPoint: number;
    csv: number;
    other: number;
    totalFiles: number;
    totalBytes: number;
    new: number;
    modified: number;
    unchanged: number;
    removed: number;
  };
};

export type SynologyInventoryPage = {
  items: Array<{
    id: string;
    fileName: string;
    extension: string;
    relativeFolderPath: string;
    relativePath: string;
    sizeBytes: number;
    lastWriteUtc: string | null;
    syncStatus: number;
    aclCaptureStatus: number;
    indexingState: string;
  }>;
  totalCount: number;
  page: number;
  pageSize: number;
};

export type ExtractionBatch = {
  id: string;
  action: string;
  status: number;
  queuedCount: number;
  processedCount: number;
  succeededCount: number;
  failedCount: number;
  skippedCount: number;
  unsupportedCount: number;
  folderPrefixNormalized: string;
  forceOutsidePilot: boolean;
  safeError: string | null;
  createdAt: string | null;
  startedAt: string | null;
  completedAt: string | null;
};

export type ExtractionStatus = {
  stats: {
    inventoryFiles: number;
    contentRows: number;
    missingContentRows: number;
    pending: number;
    indexed: number;
    failed: number;
    unsupported: number;
    skipped: number;
    queuePending: number;
    extractorVersion: number;
  };
  dependencies: {
    ocr: {
      available: boolean;
      engine: string | null;
      path: string | null;
      languages: string[];
      requiredLanguages: string[];
      missingLanguages: string[];
      installHint: string | null;
    };
    libreOffice: {
      available: boolean;
      path: string | null;
      installHint?: string | null;
    };
    extractorVersion: number;
    supportedExtensions: string[];
    limits: Record<string, number>;
  };
  recentBatches: ExtractionBatch[];
};

type StatusBlock = {
  status: string;
  error?: string | null;
  lastCheck?: string | null;
  [key: string]: unknown;
};

export type IntegrationStatus = {
  checkedAt: string;
  fastapi: StatusBlock;
  postgresql: StatusBlock;
  synologyNas: StatusBlock & { connectionCount?: number };
  synologySync: StatusBlock & { lastSuccessfulSyncAt?: string | null };
  documentIndex: StatusBlock & { inventoryCount?: number; extractedCount?: number };
  googleWorkspaceAuth: StatusBlock & { allowlistedEmails?: number };
  openaiApi?: StatusBlock & {
    configured?: boolean;
    available?: boolean;
    model?: string | null;
    dataSharingPermitted?: boolean;
    successCount?: number;
    errorCount?: number;
    lastSuccessfulRequestAt?: string | null;
    lastErrorAt?: string | null;
    lastError?: string | null;
  };
  mcpServer: StatusBlock & {
    endpoint?: string;
    authReady?: boolean;
    tools?: string[];
  };
  mcpTools: StatusBlock & {
    successCount?: number;
    failureCount?: number;
    lastAuthenticatedAt?: string | null;
  };
  secureMcpTunnel: StatusBlock;
  chatgptBusiness: StatusBlock & {
    pluginName?: string;
    environment?: string;
  };
};

export type AiStatus = {
  configured: boolean;
  available: boolean;
  status: string;
  provider?: string | null;
  connectionLabel?: string | null;
  model: string | null;
  modelAvailable?: boolean | null;
  maxOutputTokens: number;
  maxContextChars: number;
  dataSharingPermitted: boolean;
  creditStatus?: string;
  creditMessage?: string | null;
  creditBalanceReadable?: boolean;
  billingUrl?: string | null;
  successCount: number;
  errorCount: number;
  lastSuccessfulRequestAt: string | null;
  lastErrorAt: string | null;
  lastError: string | null;
  probeError?: string | null;
  message: string | null;
};

export type AiConversationSummary = {
  id: string;
  title: string;
  createdAt: string | null;
  updatedAt: string | null;
};

export type AiAttachmentRef = {
  id?: string;
  kind: string;
  documentId?: string | null;
  sampleId?: string | null;
  displayName?: string | null;
  sourceRef?: string | null;
};

export type AiMessage = {
  id: string;
  role: string;
  content: string;
  status: string;
  error?: string | null;
  createdAt?: string | null;
  attachments?: AiAttachmentRef[];
};

export type AiConversationDetail = AiConversationSummary & {
  messages: AiMessage[];
};

export type AiSampleUploadResult = {
  ok: boolean;
  id?: string;
  fileName?: string;
  extension?: string;
  sizeBytes?: number;
  status?: number;
  charCount?: number;
  error?: string | null;
  sourceRef?: string;
};

export type AiStreamMeta = {
  conversationId: string;
  userMessageId: string;
  assistantMessageId: string;
  model: string;
  sources: Array<{
    kind: string;
    documentId: string | null;
    sampleId: string | null;
    displayName: string;
    sourceRef: string;
    ok: boolean;
    error: string | null;
  }>;
};

export type AiStreamDone = {
  conversationId: string;
  assistantMessageId: string;
  usage: { inputTokens: number; outputTokens: number };
  sources: AiStreamMeta["sources"];
};

export type AdServiceIdentity = {
  samCompatible?: string;
  userPrincipalName?: string;
  dnsDomain?: string;
  username?: string;
  userDomain?: string;
  computerName?: string;
};

export type AdConnection = {
  id: string;
  connectionName: string;
  domainDnsName: string;
  domainController: string;
  protocol: string;
  authMode?: string;
  port: number;
  baseDn: string;
  bindUsername: string;
  bindSecretConfigured: boolean;
  timeoutSeconds: number;
  verifyTls: boolean;
  connectionStatus: number;
  statusLabel?: string;
  serviceIdentity?: AdServiceIdentity | null;
  lastSuccessfulTestAt?: string | null;
  lastSafeError?: string | null;
  lastSuccessfulSyncAt?: string | null;
  lastSyncResult?: string | null;
};

export type AdConnectionBody = {
  connectionName?: string;
  domainDnsName?: string;
  domainController?: string;
  authMode?: string;
  protocol?: string;
  port?: number;
  baseDn?: string;
  bindUsername?: string;
  bindPassword?: string;
  timeoutSeconds?: number;
  verifyTls?: boolean;
};

export type AdConnectionTestResult = {
  succeeded: boolean;
  safeError?: string | null;
  discoveredBaseDn?: string | null;
  authMode?: string;
  serviceIdentity?: AdServiceIdentity | null;
  ldapSigning?: boolean;
  ldapSealing?: boolean;
  checks: Array<{ name: string; status: string; detail?: string }>;
};

export type AdOverview = {
  configured: boolean;
  connection?: AdConnection | null;
  usersDiscovered: number;
  groupsDiscovered: number;
  selectedOus: number;
  linkedIdentities: number;
  pendingMappings: number;
  conflictMappings: number;
  disabledAccounts?: number;
  warnings?: string[];
};
