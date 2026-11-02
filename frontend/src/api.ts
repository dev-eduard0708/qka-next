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
  addAllowedEmail: (email: string) =>
    request<AllowedEmail>("/api/v1/access/emails", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  setAllowedEmailEnabled: (id: string, enabled: boolean) =>
    request<AllowedEmail>(`/api/v1/access/emails/${id}/enabled`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  removeAllowedEmail: (id: string) =>
    request<{ ok: boolean }>(`/api/v1/access/emails/${id}/remove`, { method: "POST" }),
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
  search: (q: string) =>
    request<{
      results: Array<{
        id: string;
        title: string;
        url: string;
        snippet: string;
        relativePath: string;
        extension: string;
        fileType: string;
      }>;
      error: string | null;
    }>(`/api/v1/search?q=${encodeURIComponent(q)}`),
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
};

export type SynologyConnection = {
  id: string;
  name: string;
  hostName: string;
  configuredSharedFolderPath: string;
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

export type SynologyJob = {
  id: string;
  trigger: number;
  status: number;
  currentOperation: string | null;
  filesDiscovered: number;
  filesProcessed: number;
  filesNew: number;
  filesModified: number;
  filesUnchanged: number;
  filesRemoved: number;
  percentComplete: number | null;
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
  model: string | null;
  maxOutputTokens: number;
  maxContextChars: number;
  dataSharingPermitted: boolean;
  successCount: number;
  errorCount: number;
  lastSuccessfulRequestAt: string | null;
  lastErrorAt: string | null;
  lastError: string | null;
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
