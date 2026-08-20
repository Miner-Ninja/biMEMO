/**
 * biMEMO unified API client.
 * CSRF token injection, 401 auto-refresh, file operations, streaming.
 */
import { z } from "zod";
import type { ZodType } from "zod";

let csrfToken: string | null = null;
const CSRF_PROBLEM_TYPE = "urn:bimemo:problem:csrf-failed";
const VALIDATION_PROBLEM_TYPE = "urn:bimemo:problem:validation-error";
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
 * - On 204 (no body), resolves to null instead of parsing JSON.
 * - Skips Content-Type for FormData bodies (browser sets multipart boundary).
 */
export async function apiRequest<T>(
  url: string,
  options: RequestInit = {},
  schema?: ZodType<T>
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
    const body = await response.json().catch(() => ({}));
    if (body.type === CSRF_PROBLEM_TYPE) {
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
      throw new ApiError(response.status, problemDetail(body, response.statusText));
    }
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, problemDetail(body, response.statusText));
  }
  if (response.status === 204) {
    return null as T;
  }

  const data: unknown = await response.json();
  if (schema === undefined) {
    return data as T;
  }
  const parsed = schema.safeParse(data);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    if (issue === undefined) {
      // Unreachable: a failed safeParse always carries at least one issue.
      throw new ContractError("Unexpected response format");
    }
    const location =
      issue.path.length > 0 ? issue.path.map((part) => String(part)).join(".") : "root";
    throw new ContractError(
      `Unexpected response format: ${issue.message} at ${location}`
    );
  }
  return parsed.data;
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

/**
 * Contract error: the HTTP request succeeded (2xx), but the response body
 * did not match the expected schema. Has no HTTP status (0).
 */
export class ContractError extends ApiError {
  constructor(message: string) {
    super(0, message);
    this.name = "ContractError";
  }
}

/**
 * Extract the user-facing message from one error response body.
 *
 * Problem+json bodies carry the human-readable text in `detail`;
 * validation errors (type urn:bimemo:problem:validation-error) carry
 * the per-field messages in the `errors` extension, which reads
 * better than the summary line.
 */
function problemDetail(body: unknown, statusText: string): string {
  if (body !== null && typeof body === "object") {
    const record = body as {
      type?: unknown;
      detail?: unknown;
      errors?: unknown;
    };
    if (record.type === VALIDATION_PROBLEM_TYPE && Array.isArray(record.errors)) {
      const messages = (record.errors as Array<{ msg?: unknown }>)
        .map((entry) => (typeof entry?.msg === "string" ? entry.msg : ""))
        .filter(Boolean);
      if (messages.length > 0) {
        return messages.join("; ");
      }
    }
    if (typeof record.detail === "string" && record.detail.length > 0) {
      return record.detail;
    }
    if (record.detail !== undefined && record.detail !== null) {
      return JSON.stringify(record.detail);
    }
  }
  return statusText;
}

/**
 * Per-conversation display settings. The backend model defaults every
 * flag (show_thinking false, show_suggestions true, thinking_enabled
 * true), so a legacy/variant response may omit a key; consumers
 * normalize absence to those same defaults.
 */
export const storedChatSettingsSchema = z.looseObject({
  show_thinking: z.boolean().optional(),
  show_suggestions: z.boolean().optional(),
  /** Reasoning generation on/off; also gates thinking-block display. */
  thinking_enabled: z.boolean().optional(),
});

export type StoredChatSettings = z.infer<typeof storedChatSettingsSchema>;

/** One persisted conversation row as returned by the conversation routes. */
export const storedConversationSchema = z.looseObject({
  id: z.string(),
  title: z.string(),
  /** Always present; null when the conversation has no active bridge link. */
  bridge_provider: z.string().nullable(),
  // Backend defaults: settings is a default-constructed settings
  // object, processing_status "idle", processing_stage "".
  settings: storedChatSettingsSchema.optional(),
  processing_status: z.string().optional(),
  processing_stage: z.string().optional(),
  processing_started_at: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});

export type StoredConversation = z.infer<typeof storedConversationSchema>;

/** One content part of a persisted message (text or image_url part). */
const storedMessageContentPartSchema = z.looseObject({
  type: z.string(),
  text: z.string().optional(),
  image_url: z.looseObject({ url: z.string().optional() }).optional(),
});

/**
 * Wire shape of one persisted message. The backend declares `role` as a
 * free-form string (the database check restricts it to
 * user/assistant/system/tool) and content parts as free-form objects, so
 * this schema stays as broad as the backend.
 */
export const storedConversationMessageSchema = z.looseObject({
  id: z.string(),
  role: z.string(),
  content: z.string().or(z.array(storedMessageContentPartSchema)),
  conversation_id: z.string(),
  created_at: z.string(),
  // Backend fields with defaults: present on the wire as null/[] when
  // empty, accepted as absent as well (serialization can omit defaults).
  meta: z.string().nullable().optional(),
  structured_content: z
    .looseObject({
      answer: z.string(),
      // Present whenever structured_content is: the backend model
      // defaults thinking to null and suggestions to [].
      thinking: z.string().nullable(),
      suggestions: z.array(z.string()),
    })
    .nullable()
    .optional(),
  used_tools: z
    .array(
      z.looseObject({
        name: z.string(),
        permission_mode: z.string().optional(),
        authorization_source: z.string().optional(),
        duration_ms: z.number().nullable().optional(),
        success: z.boolean(),
      })
    )
    .optional(),
  pending_confirmations: z
    .array(
      z.looseObject({
        action_id: z.string(),
        tool_name: z.string(),
        parameters: z.record(z.string(), z.unknown()),
        // Present whenever the item is: the backend model defaults
        // grant_capable to false and url_host to null.
        grant_capable: z.boolean(),
        url_host: z.string().nullable(),
      })
    )
    .optional(),
});

type StoredMessageContentPart = {
  type: string;
  text?: string;
  image_url?: { url?: string };
};

/**
 * One persisted conversation message in the shape the chat UI consumes.
 * Consumer projection of storedConversationMessageSchema: the backend
 * declares `role` as a free-form string (the db check allows
 * user/assistant/system/tool) and content parts as free-form objects,
 * while the UI only renders user/assistant text/image parts, so the
 * narrow shapes stay here and fetchConversationMessages casts them.
 */
export interface StoredConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string | StoredMessageContentPart[];
  conversation_id: string;
  created_at: string;
  meta: string | null;
  // Explicit `| undefined`: with exactOptionalPropertyTypes the wire
  // value passes through as-is (absent on the wire stays absent here).
  structured_content?:
    | {
        answer: string;
        thinking?: string | null;
        suggestions?: string[];
      }
    | null
    | undefined;
  pending_confirmations?:
    | {
        action_id: string;
        tool_name: string;
        parameters: Record<string, unknown>;
        grant_capable?: boolean;
        url_host?: string | null;
      }[]
    | undefined;
  used_tools?:
    | {
        name: string;
        permission_mode: string;
        authorization_source: string;
        success: boolean;
      }[]
    | undefined;
}

/** Body of POST /chat/conversations. */
export const conversationCreateResponseSchema = z.looseObject({
  conversation_id: z.string(),
});

export type ConversationCreateResponse = z.infer<
  typeof conversationCreateResponseSchema
>;

/** One configured external bridge provider that can be chosen in the web UI. */
export const chatBridgeProviderItemSchema = z.looseObject({
  provider: z.string(),
  // The backend builder defaults this to an empty string.
  default_persona_user_id: z.string().optional(),
});

export type ChatBridgeProviderItem = z.infer<typeof chatBridgeProviderItemSchema>;

/** One stored external chat bridge link. */
export const chatBridgeLinkSchema = z.looseObject({
  id: z.string(),
  conversation_id: z.string(),
  provider: z.string(),
  external_space_id: z.string(),
  external_channel_id: z.string(),
  created_by: z.string(),
  sync_direction: z.string(),
  status: z.string(),
  delivery_mode: z.string(),
  persona_mode: z.string(),
  persona_user_id: z.string(),
  created_at: z.string(),
});

export type ChatBridgeLink = z.infer<typeof chatBridgeLinkSchema>;

/** One stored bridge message mapping. */
export const chatBridgeMessageLinkSchema = z.looseObject({
  id: z.string(),
  bridge_link_id: z.string(),
  message_id: z.string(),
  external_message_id: z.string(),
  direction: z.string(),
  created_at: z.string(),
});

export type ChatBridgeMessageLink = z.infer<typeof chatBridgeMessageLinkSchema>;

/** Create one persisted conversation on the server. */
export async function createConversation(
  title = ""
): Promise<ConversationCreateResponse> {
  return apiRequest(
    "/chat/conversations",
    {
      method: "POST",
      body: JSON.stringify({ title }),
    },
    conversationCreateResponseSchema
  );
}

/** Load the persisted conversation list from the server. */
export async function fetchConversations(): Promise<StoredConversation[]> {
  return apiRequest("/chat/conversations", {}, z.array(storedConversationSchema));
}

/** One recent run shown in the trace picker. */
export const traceRunSummarySchema = z.looseObject({
  id: z.string(),
  conversation_id: z.string(),
  conversation_title: z.string(),
  channel: z.string(),
  status: z.string(),
  created_at: z.string(),
  /** The run's request text, truncated to 200 chars server-side. */
  user_message: z.string(),
});

export type TraceRunSummary = z.infer<typeof traceRunSummarySchema>;

/** One agent_runs row as returned by the db proxy. */
export const agentRunRecordSchema = z.looseObject({
  id: z.string(),
  conversation_id: z.string(),
  session_id: z.string(),
  channel: z.string(),
  status: z.string(),
  current_step: z.string(),
  request_id: z.string(),
  // Stored as a JSON text column; parsed to an object by the db proxy.
  resume_payload: z.looseObject({}),
  tool_policy_version: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  completed_at: z.string().nullable(),
});

/** One agent_run_events row as returned by the db proxy. */
export const agentRunEventRecordSchema = z.looseObject({
  id: z.string(),
  run_id: z.string(),
  request_id: z.string(),
  conversation_id: z.string(),
  sequence: z.number(),
  event_type: z.string(),
  // Parsed JSON object; free-form per event type.
  payload: z.looseObject({}),
  created_at: z.string(),
  delivered_at: z.string().nullable(),
});

/** One agent_confirmations row joined with its tool invocation. */
export const agentConfirmationRecordSchema = z.looseObject({
  confirmation_id: z.string(),
  turn_id: z.string(),
  invocation_id: z.string(),
  status: z.string(),
  title: z.string(),
  description: z.string(),
  expires_at: z.string(),
  decided_at: z.string().nullable(),
  created_at: z.string(),
  tool_name: z.string(),
  arguments: z.looseObject({}),
});

/** Full per-turn trace assembled from the persisted run tables. */
export const turnTraceSchema = z.looseObject({
  run: agentRunRecordSchema,
  conversation_id: z.string(),
  messages: z.looseObject({
    user: z
      .looseObject({
        content: z.string(),
        created_at: z.string(),
      })
      .nullable(),
    assistant: z
      .looseObject({
        content: z.string(),
        created_at: z.string(),
      })
      .nullable(),
  }),
  // Map of signal event type to that event's parsed payload.
  signals: z.record(z.string(), z.looseObject({})),
  tool_calls: z.array(agentRunEventRecordSchema),
  confirmations: z.array(agentConfirmationRecordSchema),
  events: z.array(agentRunEventRecordSchema),
});

export type TurnTrace = z.infer<typeof turnTraceSchema>;

/** Body of GET /admin/trace (recent runs for the picker). */
export const recentTracesResponseSchema = z.looseObject({
  runs: z.array(traceRunSummarySchema),
});

/** Load recent runs (newest first) with their request text for the picker. */
export async function getRecentTraces(limit = 50): Promise<TraceRunSummary[]> {
  const data = await apiRequest(
    `/admin/trace?limit=${limit}`,
    {},
    recentTracesResponseSchema
  );
  return data.runs;
}

/** Load the full turn trace for one agent run id. */
export async function getTurnTrace(runId: string): Promise<TurnTrace> {
  return apiRequest(`/admin/trace/${encodePathSegment(runId)}`, {}, turnTraceSchema);
}

/**
 * One page of a persisted conversation history, newest messages first.
 *
 * `next_cursor` is an opaque pagination token: the client echoes it back
 * verbatim on the next request to load the older page, without parsing it.
 */
export interface ConversationMessagePage {
  messages: StoredConversationMessage[];
  next_cursor: string | null;
  has_more: boolean;
}

/**
 * Wire body of GET /chat/conversations/{id}/messages. Items use
 * storedConversationMessageSchema (backend shape); the wrapper converts
 * them to the consumer page type above.
 */
export const conversationMessagePageSchema = z.looseObject({
  messages: z.array(storedConversationMessageSchema),
  next_cursor: z.string().nullable().optional(),
  has_more: z.boolean().optional(),
});

/** Load one page of a persisted conversation history from the server. */
export async function fetchConversationMessages(
  conversationId: string,
  limit = 200,
  cursor?: string | null
): Promise<ConversationMessagePage> {
  const params = new URLSearchParams({
    limit: String(limit),
  });
  if (cursor) {
    params.set("cursor", cursor);
  }
  const page = await apiRequest(
    `/chat/conversations/${encodePathSegment(conversationId)}/messages?${params.toString()}`,
    {},
    conversationMessagePageSchema
  );
  return {
    next_cursor: page.next_cursor ?? null,
    has_more: page.has_more ?? false,
    messages: page.messages.map((message) => ({
      id: message.id,
      // The backend role is a free-form string (the db check allows
      // user/assistant/system/tool); the UI only renders user/assistant,
      // so the narrow union is cast here.
      role: message.role as StoredConversationMessage["role"],
      // The backend content parts are free-form objects; the narrow part
      // shape above is the one the chat UI renders.
      content: message.content as StoredConversationMessage["content"],
      conversation_id: message.conversation_id,
      created_at: message.created_at,
      meta: message.meta ?? null,
      structured_content: message.structured_content,
      pending_confirmations: message.pending_confirmations,
      used_tools: message.used_tools?.map((tool) => ({
        name: tool.name,
        permission_mode: tool.permission_mode ?? "",
        authorization_source: tool.authorization_source ?? "",
        success: tool.success,
      })),
    })),
  };
}

export async function updateConversationTitle(
  conversationId: string,
  title: string
): Promise<StoredConversation> {
  return apiRequest(
    `/chat/conversations/${encodePathSegment(conversationId)}`,
    {
      method: "PUT",
      body: JSON.stringify({ title }),
    },
    storedConversationSchema
  );
}

/** Delete one persisted conversation on the server. */
export async function deleteConversation(conversationId: string): Promise<void> {
  await apiRequest(`/chat/conversations/${encodePathSegment(conversationId)}`, {
    method: "DELETE",
  });
}

/** Update per-conversation display settings. */
export async function updateChatSettings(
  conversationId: string,
  settings: Partial<StoredChatSettings>
): Promise<StoredChatSettings> {
  return apiRequest(
    `/chat/conversations/${encodePathSegment(conversationId)}/settings`,
    {
      method: "PATCH",
      body: JSON.stringify(settings),
    },
    storedChatSettingsSchema
  );
}

/** Load bridge providers that the web chat can connect to. */
export async function fetchChatBridgeProviders(): Promise<ChatBridgeProviderItem[]> {
  return apiRequest(
    "/admin/chat-bridge-providers",
    {},
    z.array(chatBridgeProviderItemSchema)
  );
}

/**
 * Body of GET /admin/chat-bridges/conversations/{id} and
 * PUT /admin/chat-bridges/{id}/settings: either the stored link or an
 * explicit `chat_bridge_link: null` marker.
 */
export const chatBridgeLinkLookupSchema = z.union([
  chatBridgeLinkSchema,
  z.looseObject({ chat_bridge_link: z.null() }),
]);

/** Load the stored bridge link for one conversation, if it exists. */
export async function fetchChatBridgeForConversation(
  conversationId: string
): Promise<ChatBridgeLink | null> {
  const response = await apiRequest(
    `/admin/chat-bridges/conversations/${encodePathSegment(conversationId)}`,
    {},
    chatBridgeLinkLookupSchema
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
  return apiRequest(url, {}, z.array(chatBridgeLinkSchema));
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
  return apiRequest(url, {}, z.array(chatBridgeMessageLinkSchema));
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
  return apiRequest(
    "/admin/chat-bridges",
    {
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
    },
    chatBridgeLinkSchema
  );
}

/**
 * Body of POST /admin/chat-bridges/{id}/disable and /enable.
 * `chat_bridge_link` is only present when the toggle changed the link.
 */
export const chatBridgeToggleResponseSchema = z.looseObject({
  updated: z.boolean(),
  chat_bridge_link: chatBridgeLinkSchema.optional(),
});

/** Disable one existing bridge link. */
export async function disableChatBridgeLink(
  bridgeLinkId: string,
  options: ChatBridgeMutationOptions
): Promise<boolean> {
  const response = await apiRequest(
    `/admin/chat-bridges/${encodePathSegment(bridgeLinkId)}/disable`,
    {
      method: "POST",
      headers: { "X-Reauth-Token": options.reauthToken },
    },
    chatBridgeToggleResponseSchema
  );
  return response.updated;
}

/** Enable one existing bridge link again. */
export async function enableChatBridgeLink(
  bridgeLinkId: string,
  options: ChatBridgeMutationOptions
): Promise<boolean> {
  const response = await apiRequest(
    `/admin/chat-bridges/${encodePathSegment(bridgeLinkId)}/enable`,
    {
      method: "POST",
      headers: { "X-Reauth-Token": options.reauthToken },
    },
    chatBridgeToggleResponseSchema
  );
  return response.updated;
}

/** Update one existing bridge link presentation mode. */
export async function updateChatBridgeLinkSettings(
  bridgeLinkId: string,
  options: ChatBridgeUpdateOptions
): Promise<ChatBridgeLink | null> {
  const response = await apiRequest(
    `/admin/chat-bridges/${encodePathSegment(bridgeLinkId)}/settings`,
    {
      method: "PUT",
      headers: { "X-Reauth-Token": options.reauthToken },
      body: JSON.stringify({
        delivery_mode: options.deliveryMode,
        persona_mode: options.personaMode,
        persona_user_id: options.personaUserId,
      }),
    },
    chatBridgeLinkLookupSchema
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

/** Body of POST /files/upload (the stored file record). */
export const uploadFileResponseSchema = z.looseObject({
  id: z.string(),
  original_name: z.string(),
  size: z.number(),
  mime_type: z.string(),
});

/** One row of the files table as returned by the db proxy. */
export const fileMetadataRecordSchema = z.looseObject({
  id: z.string(),
  original_name: z.string(),
  size: z.number(),
  mime_type: z.string(),
  // NOT NULL with a database default, so always a string.
  created_at: z.string(),
});

/** Body of GET /files. */
export const listFilesResponseSchema = z.looseObject({
  files: z.array(fileMetadataRecordSchema),
});

/** Upload a file. */
export async function uploadFile(file: File): Promise<FileUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const data = await apiRequest(
    "/files/upload",
    {
      method: "POST",
      body: formData,
    },
    uploadFileResponseSchema
  );

  return {
    file_id: data.id,
    filename: data.original_name,
    size: data.size,
    status: "indexed",
  };
}

/** Fetch list of uploaded files. */
export async function fetchFiles(): Promise<IndexedFile[]> {
  const data = await apiRequest("/files", {}, listFilesResponseSchema);
  return data.files.map((file) => ({
    file_id: file.id,
    filename: file.original_name,
    size: file.size,
    mime_type: file.mime_type,
    created_at: file.created_at,
  }));
}

/** Delete a file by ID. */
export async function deleteFileById(fileId: string): Promise<void> {
  await apiRequest(`/files/${encodePathSegment(fileId)}`, { method: "DELETE" });
}

// ============================================================
// Workspace
// ============================================================

/** One entry of the workspace directory tree. */
export const workspaceItemSchema = z.looseObject({
  name: z.string(),
  type: z.enum(["file", "directory"]),
  path: z.string(),
  // Present only for files.
  size: z.number().optional(),
});

export type WorkspaceItem = z.infer<typeof workspaceItemSchema>;

/** Body of GET /workspace/tree. */
export const workspaceTreeSchema = z.looseObject({
  items: z.array(workspaceItemSchema),
  path: z.string(),
  parent: z.string().nullable(),
  // The backend omits this when no workspace directories are configured.
  workspace: z.string().optional(),
});

export type WorkspaceTreeResponse = z.infer<typeof workspaceTreeSchema>;

/** Fetch workspace directory tree. */
export async function fetchWorkspaceTree(
  path: string = ""
): Promise<WorkspaceTreeResponse> {
  const params = path ? `?path=${encodeURIComponent(path)}` : "";
  return apiRequest(`/workspace/tree${params}`, {}, workspaceTreeSchema);
}

/** Body of GET /workspace/dirs. */
export const workspaceDirsSchema = z.looseObject({
  directories: z.array(z.string()),
});

/** Fetch configured workspace directories. */
export async function fetchWorkspaceDirs(): Promise<string[]> {
  const data = await apiRequest("/workspace/dirs", {}, workspaceDirsSchema);
  return data.directories;
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
    const body = await response.json().catch(() => ({}));
    if (body.type === CSRF_PROBLEM_TYPE) {
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
      throw new ApiError(response.status, problemDetail(body, response.statusText));
    }
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, problemDetail(body, response.statusText));
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

const UNKNOWN_ERROR_SUGGESTION = "Try repeating your request or start a new chat";

const ERROR_SUGGESTIONS: Record<string, string> = {
  ai_unavailable: "Check that Ollama is running and try again in a minute",
  ai_timeout: "Try asking a shorter question or try again",
  db_error: "Try rephrasing your question",
  file_search_error: "Check that the file was uploaded and try again",
  mcp_unavailable: "Try refreshing the page or wait a minute",
  unknown: UNKNOWN_ERROR_SUGGESTION,
};

export function getErrorSuggestion(category: string): string {
  return ERROR_SUGGESTIONS[category] ?? UNKNOWN_ERROR_SUGGESTION;
}

// ============================================================
// RED zone tool confirmations
// ============================================================

/**
 * Body of POST /confirm. `status`, `message_id` and `result_summary` are
 * only present when the backend reports a non-empty value for them;
 * `status` is the turn outcome after the decision:
 * final | paused | failed | cancelled.
 */
export const confirmResponseSchema = z.looseObject({
  accepted: z.boolean(),
  detail: z.string(),
  action_id: z.string(),
  status: z.string().optional(),
  message_id: z.string().optional(),
  result_summary: z.string().optional(),
});

export type ConfirmResponse = z.infer<typeof confirmResponseSchema>;

/** Approve a pending RED zone tool action. */
export async function confirmAction(
  actionId: string,
  scope: "once" | "domain" | "conversation",
  conversationId: string = ""
): Promise<ConfirmResponse> {
  return apiRequest(
    "/confirm",
    {
      method: "POST",
      body: JSON.stringify({
        action_id: actionId,
        decision: "approve",
        scope: scope,
        conversation_id: conversationId,
      }),
    },
    confirmResponseSchema
  );
}

/** Deny a pending RED zone tool action. */
export async function denyAction(
  actionId: string,
  conversationId: string = ""
): Promise<ConfirmResponse> {
  return apiRequest(
    "/confirm",
    {
      method: "POST",
      body: JSON.stringify({
        action_id: actionId,
        decision: "deny",
        conversation_id: conversationId,
      }),
    },
    confirmResponseSchema
  );
}

// ============================================================
// Chat streaming
// ============================================================

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}
