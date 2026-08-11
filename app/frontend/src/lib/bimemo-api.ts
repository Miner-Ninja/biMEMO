/**
 * biMEMO unified API client.
 * CSRF token injection, 401 auto-refresh, file operations, streaming.
 */

let csrfToken: string | null = null;
const CSRF_FAILURE_DETAIL = "CSRF validation failed";
const STARTUP_READY_POLL_MS = 1000;
const STARTUP_READY_TIMEOUT_MS = 60000;

/** Encode user-controlled path segments before interpolating them into URLs. */
export function encodePathSegment(value: string): string {
  return encodeURIComponent(value);
}

/** Fetch a fresh CSRF token from the server. */
async function fetchCsrfToken(): Promise<string> {
  let response = await fetch("/csrf-token", { credentials: "include" });
  if (response.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      response = await fetch("/csrf-token", { credentials: "include" });
    }
  }
  if (!response.ok) throw new Error("Failed to fetch CSRF token");
  const data = await response.json();
  csrfToken = data.token as string;
  return csrfToken as string;
}

/** Attempt to refresh the access token. */
export async function refreshAccessToken(): Promise<boolean> {
  clearCsrfToken();
  const response = await fetch("/refresh", {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) {
    clearCsrfToken();
  }
  return response.ok;
}

/** Clear cached CSRF token (e.g., on logout). */
export function clearCsrfToken(): void {
  csrfToken = null;
}

// ============================================================
// Auth API functions
// ============================================================

export interface AuthStatus {
  needs_setup: boolean;
  authenticated: boolean;
  startup_state?: string;
  ready?: boolean;
  detail?: string;
}

export interface AuthActionResult {
  status?: string;
}

/** Check auth status: needs_setup and authenticated flags. */
export async function authStatus(): Promise<AuthStatus> {
  const response = await fetch("/auth/status", { credentials: "include" });
  if (!response.ok) throw new ApiError(response.status, "Failed to check auth status");
  return response.json();
}

/** Create password on first launch. */
export async function authSetup(password: string): Promise<AuthActionResult> {
  const response = await fetch("/auth/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
    credentials: "include",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, body.detail || response.statusText);
  }
  return response.json().catch(() => ({}));
}

/** Login with password. */
export async function authLogin(password: string): Promise<AuthActionResult> {
  const response = await fetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
    credentials: "include",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const apiError = new ApiError(response.status, body.detail || response.statusText);
    apiError.retryAfter = response.headers.get("Retry-After");
    throw apiError;
  }
  return response.json().catch(() => ({}));
}

/** Wait until the full application replaces the startup gate. */
export async function waitForApplicationReady(): Promise<void> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < STARTUP_READY_TIMEOUT_MS) {
    const response = await fetch("/health", { credentials: "include" }).catch(
      () => null
    );
    if (response?.ok) {
      const body = await response.json().catch(() => ({}));
      if (body.ready !== false && body.status === "ok") {
        return;
      }
    }
    await new Promise((resolve) => window.setTimeout(resolve, STARTUP_READY_POLL_MS));
  }

  throw new Error("Application did not finish starting in time");
}

// ============================================================
// Generic API request with CSRF + auto-refresh
// ============================================================

/**
 * Make an API request with CSRF and auto-refresh handling.
 *
 * - Injects X-CSRF-Token header on state-changing methods.
 * - On 401, attempts token refresh and retries once.
 * - Skips Content-Type for FormData bodies (browser sets multipart boundary).
 */
export async function apiRequest<T>(
  url: string,
  options: RequestInit = {}
): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers);
  const isFormData = options.body instanceof FormData;

  if (!headers.has("Content-Type") && method !== "GET" && !isFormData) {
    headers.set("Content-Type", "application/json");
  }

  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    if (!csrfToken) {
      await fetchCsrfToken();
    }
    if (csrfToken) {
      headers.set("X-CSRF-Token", csrfToken);
    }
  }

  let response = await fetch(url, {
    ...options,
    headers,
    credentials: "include",
  });

  if (response.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      // Re-fetch CSRF after refresh
      await fetchCsrfToken();
      if (csrfToken && ["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
        headers.set("X-CSRF-Token", csrfToken);
      }
      response = await fetch(url, {
        ...options,
        headers,
        credentials: "include",
      });
    } else {
      throw new ApiError(401, "Authentication required");
    }
  }

  if (response.status === 403 && ["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const detail =
      typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    if (detail === CSRF_FAILURE_DETAIL) {
      clearCsrfToken();
      await fetchCsrfToken();
      if (csrfToken) {
        headers.set("X-CSRF-Token", csrfToken);
      }
      response = await fetch(url, {
        ...options,
        headers,
        credentials: "include",
      });
    } else {
      throw new ApiError(response.status, detail || response.statusText);
    }
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const detail =
      typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    throw new ApiError(response.status, detail || response.statusText);
  }

  return response.json();
}

/** API error with HTTP status code. */
export class ApiError extends Error {
  status: number;
  retryAfter: string | null;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = null;
  }
}

export interface StoredChatSettings {
  show_thinking: boolean;
  show_suggestions: boolean;
  /** Reasoning generation on/off; also gates thinking-block display. */
  thinking_enabled: boolean;
}

export interface StoredConversation {
  id: string;
  title: string;
  bridge_provider?: string | null;
  settings?: StoredChatSettings;
  processing_status: string;
  processing_stage: string;
  processing_started_at: string | null;
  created_at: string;
  updated_at: string;
}

type StoredMessageContentPart =
  { type: "text"; text?: string } | { type: "image_url"; image_url?: { url?: string } };

export interface StoredConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string | StoredMessageContentPart[];
  conversation_id: string;
  created_at: string;
  meta: string | null;
  structured_content?: {
    answer: string;
    thinking?: string | null;
    sql?: string | null;
    suggestions?: string[];
  } | null;
}

export interface ConversationCreateResponse {
  conversation_id: string;
}

export interface ConversationDeleteResponse {
  ok: boolean;
}

export interface ChatBridgeProviderItem {
  provider: string;
  default_persona_user_id: string;
}

export interface ChatBridgeLink {
  id: string;
  conversation_id: string;
  provider: string;
  external_space_id: string;
  external_channel_id: string;
  created_by: string;
  sync_direction: string;
  status: string;
  delivery_mode: string;
  persona_mode: string;
  persona_user_id: string;
  created_at: string;
}

export interface ChatBridgeMessageLink {
  id: string;
  bridge_link_id: string;
  message_id: string;
  external_message_id: string;
  direction: string;
  created_at: string;
}

/** Create one persisted conversation on the server. */
export async function createConversation(
  title = ""
): Promise<ConversationCreateResponse> {
  return apiRequest<ConversationCreateResponse>("/chat/conversations", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

/** Load the persisted conversation list from the server. */
export async function fetchConversations(): Promise<StoredConversation[]> {
  return apiRequest<StoredConversation[]>("/chat/conversations");
}

/** Load one persisted conversation history from the server. */
export async function fetchConversationMessages(
  conversationId: string,
  limit = 200,
  offset = 0
): Promise<StoredConversationMessage[]> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return apiRequest<StoredConversationMessage[]>(
    `/chat/conversations/${encodePathSegment(conversationId)}/messages?${params.toString()}`
  );
}

/** Rename one persisted conversation on the server. */
export async function updateConversationTitle(
  conversationId: string,
  title: string
): Promise<StoredConversation> {
  return apiRequest<StoredConversation>(
    `/chat/conversations/${encodePathSegment(conversationId)}`,
    {
      method: "PUT",
      body: JSON.stringify({ title }),
    }
  );
}

/** Delete one persisted conversation on the server. */
export async function deleteConversation(
  conversationId: string
): Promise<ConversationDeleteResponse> {
  return apiRequest<ConversationDeleteResponse>(
    `/chat/conversations/${encodePathSegment(conversationId)}`,
    { method: "DELETE" }
  );
}

/** Update per-conversation display settings. */
export async function updateChatSettings(
  conversationId: string,
  settings: Partial<StoredChatSettings>
): Promise<StoredChatSettings> {
  return apiRequest<StoredChatSettings>(
    `/chat/conversations/${encodePathSegment(conversationId)}/settings`,
    {
      method: "PATCH",
      body: JSON.stringify(settings),
    }
  );
}

/** Load bridge providers that the web chat can connect to. */
export async function fetchChatBridgeProviders(): Promise<ChatBridgeProviderItem[]> {
  return apiRequest<ChatBridgeProviderItem[]>("/admin/chat-bridge-providers");
}

/** Load the stored bridge link for one conversation, if it exists. */
export async function fetchChatBridgeForConversation(
  conversationId: string
): Promise<ChatBridgeLink | null> {
  const response = await apiRequest<ChatBridgeLink | { chat_bridge_link: null }>(
    `/admin/chat-bridges/conversations/${encodePathSegment(conversationId)}`
  );
  if ("chat_bridge_link" in response) {
    return null;
  }
  return response;
}

/** Load stored bridge links, optionally limited to one conversation. */
export async function fetchChatBridgeLinks(
  conversationId?: string
): Promise<ChatBridgeLink[]> {
  const params = new URLSearchParams();
  if (conversationId) {
    params.set("conversation_id", conversationId);
  }
  const queryString = params.toString();
  const url = queryString
    ? `/admin/chat-bridges?${queryString}`
    : "/admin/chat-bridges";
  return apiRequest<ChatBridgeLink[]>(url);
}

/** Load stored bridge message mappings for one bridge link. */
export async function fetchChatBridgeMessageLinks(
  bridgeLinkId: string,
  direction?: string
): Promise<ChatBridgeMessageLink[]> {
  const params = new URLSearchParams();
  if (direction) {
    params.set("direction", direction);
  }
  const queryString = params.toString();
  const url = queryString
    ? `/admin/chat-bridges/${encodePathSegment(bridgeLinkId)}/messages?${queryString}`
    : `/admin/chat-bridges/${encodePathSegment(bridgeLinkId)}/messages`;
  return apiRequest<ChatBridgeMessageLink[]>(url);
}

type ChatBridgeMutationOptions = {
  reauthToken: string;
};

type ChatBridgeCreateOptions = ChatBridgeMutationOptions & {
  deliveryMode?: string;
  personaMode?: string;
  personaUserId?: string;
};

type ChatBridgeUpdateOptions = ChatBridgeMutationOptions & {
  deliveryMode: string;
  personaMode: string;
  personaUserId: string;
};

/** Create one new bridge link for a conversation. */
export async function createChatBridgeLink(
  conversationId: string,
  provider: string,
  options: ChatBridgeCreateOptions,
  title = ""
): Promise<ChatBridgeLink> {
  return apiRequest<ChatBridgeLink>("/admin/chat-bridges", {
    method: "POST",
    headers: { "X-Reauth-Token": options.reauthToken },
    body: JSON.stringify({
      conversation_id: conversationId,
      provider,
      title,
      delivery_mode: options.deliveryMode,
      persona_mode: options.personaMode,
      persona_user_id: options.personaUserId,
    }),
  });
}

/** Disable one existing bridge link. */
export async function disableChatBridgeLink(
  bridgeLinkId: string,
  options: ChatBridgeMutationOptions
): Promise<boolean> {
  const response = await apiRequest<{ updated: boolean }>(
    `/admin/chat-bridges/${encodePathSegment(bridgeLinkId)}/disable`,
    {
      method: "POST",
      headers: { "X-Reauth-Token": options.reauthToken },
    }
  );
  return response.updated;
}

/** Enable one existing bridge link again. */
export async function enableChatBridgeLink(
  bridgeLinkId: string,
  options: ChatBridgeMutationOptions
): Promise<boolean> {
  const response = await apiRequest<{ updated: boolean }>(
    `/admin/chat-bridges/${encodePathSegment(bridgeLinkId)}/enable`,
    {
      method: "POST",
      headers: { "X-Reauth-Token": options.reauthToken },
    }
  );
  return response.updated;
}

/** Update one existing bridge link presentation mode. */
export async function updateChatBridgeLinkSettings(
  bridgeLinkId: string,
  options: ChatBridgeUpdateOptions
): Promise<ChatBridgeLink | null> {
  const response = await apiRequest<ChatBridgeLink | { chat_bridge_link: null }>(
    `/admin/chat-bridges/${encodePathSegment(bridgeLinkId)}/settings`,
    {
      method: "POST",
      headers: { "X-Reauth-Token": options.reauthToken },
      body: JSON.stringify({
        delivery_mode: options.deliveryMode,
        persona_mode: options.personaMode,
        persona_user_id: options.personaUserId,
      }),
    }
  );
  if ("chat_bridge_link" in response) {
    return null;
  }
  return response;
}

// ============================================================
// File operations
// ============================================================

export interface FileUploadResponse {
  file_id: string;
  filename: string;
  size: number;
  status: "indexed";
}

export interface IndexedFile {
  file_id: string;
  filename: string;
  size: number;
  mime_type: string;
  created_at: string | null;
}

/** Upload a file. */
export async function uploadFile(file: File): Promise<FileUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const data = await apiRequest<{
    id: string;
    original_name: string;
    size: number;
  }>("/files/upload", {
    method: "POST",
    body: formData,
  });

  return {
    file_id: data.id,
    filename: data.original_name,
    size: data.size,
    status: "indexed",
  };
}

/** Fetch list of uploaded files. */
export async function fetchFiles(): Promise<IndexedFile[]> {
  const data = await apiRequest<{ files: Array<Record<string, unknown>> }>("/files");
  return (data.files || []).map((f) => ({
    file_id: f.id as string,
    filename: f.original_name as string,
    size: f.size as number,
    mime_type: f.mime_type as string,
    created_at: (f.created_at as string | null) ?? null,
  }));
}

/** Delete a file by ID. */
export async function deleteFileById(fileId: string): Promise<void> {
  await apiRequest(`/files/${encodePathSegment(fileId)}`, { method: "DELETE" });
}

// ============================================================
// Workspace
// ============================================================

export interface WorkspaceItem {
  name: string;
  type: "file" | "directory";
  path: string;
  size?: number;
}

export interface WorkspaceTreeResponse {
  items: WorkspaceItem[];
  path: string;
  parent: string | null;
  workspace: string;
}

/** Fetch workspace directory tree. */
export async function fetchWorkspaceTree(
  path: string = ""
): Promise<WorkspaceTreeResponse> {
  const params = path ? `?path=${encodeURIComponent(path)}` : "";
  return apiRequest<WorkspaceTreeResponse>(`/workspace/tree${params}`);
}

/** Fetch configured workspace directories. */
export async function fetchWorkspaceDirs(): Promise<string[]> {
  const data = await apiRequest<{ directories: string[] }>("/workspace/dirs");
  return data.directories || [];
}

// ============================================================
// TTS (Text-to-Speech)
// ============================================================

/** Check if TTS is available on the backend. */
export async function checkTtsStatus(): Promise<boolean> {
  try {
    const response = await fetch("/tts/status", { credentials: "include" });
    return response.ok;
  } catch {
    return false;
  }
}

/** Synthesize text to audio. Returns a Blob URL for playback. */
export async function synthesizeSpeech(text: string): Promise<string> {
  const method = "POST";
  const headers = new Headers({ "Content-Type": "application/json" });

  if (!csrfToken) {
    await fetchCsrfToken();
  }
  if (csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  let response = await fetch("/tts", {
    method,
    headers,
    credentials: "include",
    body: JSON.stringify({ text }),
  });

  if (response.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      await fetchCsrfToken();
      if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
      response = await fetch("/tts", {
        method,
        headers,
        credentials: "include",
        body: JSON.stringify({ text }),
      });
    } else {
      throw new ApiError(401, "Authentication required");
    }
  }

  if (response.status === 403) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const detail =
      typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    if (detail === CSRF_FAILURE_DETAIL) {
      clearCsrfToken();
      await fetchCsrfToken();
      if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
      response = await fetch("/tts", {
        method,
        headers,
        credentials: "include",
        body: JSON.stringify({ text }),
      });
    } else {
      throw new ApiError(response.status, detail || response.statusText);
    }
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, body.detail || response.statusText);
  }

  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

// ============================================================
// Health & error helpers
// ============================================================

/** Check if the API server is available. */
export async function checkConnection(): Promise<boolean> {
  try {
    const response = await fetch("/health", {
      method: "GET",
      credentials: "include",
      signal: AbortSignal.timeout(5000),
    });
    const data = await response.json();
    return data.status === "healthy" || data.status === "degraded";
  } catch {
    return false;
  }
}

export interface ErrorInfo {
  category: string;
  detail: string;
  message: string;
}

const ERROR_SUGGESTIONS: Record<string, string> = {
  ai_unavailable: "Check that Ollama is running and try again in a minute",
  ai_timeout: "Try asking a shorter question or try again",
  db_error: "Try rephrasing your question",
  file_search_error: "Check that the file was uploaded and try again",
  mcp_unavailable: "Try refreshing the page or wait a minute",
  unknown: "Try repeating your request or start a new chat",
};

export function getErrorSuggestion(category: string): string {
  return ERROR_SUGGESTIONS[category] || ERROR_SUGGESTIONS.unknown;
}

// ============================================================
// RED zone tool confirmations
// ============================================================

export interface ConfirmResponse {
  accepted: boolean;
  action_id: string;
}

/** Approve a pending RED zone tool action. */
export async function confirmAction(actionId: string): Promise<ConfirmResponse> {
  return apiRequest<ConfirmResponse>("/chat/confirm", {
    method: "POST",
    body: JSON.stringify({
      action_id: actionId,
      decision: "approve",
    }),
  });
}

/** Deny a pending RED zone tool action. */
export async function denyAction(actionId: string): Promise<ConfirmResponse> {
  return apiRequest<ConfirmResponse>("/chat/confirm", {
    method: "POST",
    body: JSON.stringify({
      action_id: actionId,
      decision: "deny",
    }),
  });
}

// ============================================================
// Chat streaming
// ============================================================

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}
