import { z } from "zod";
import { apiRequest, encodePathSegment } from "@/lib/bimemo-api";

const providerTypes = ["openrouter", "ollama", "custom"] as const;
export type ProviderType = (typeof providerTypes)[number];

export interface SaveLLMConfigParams {
  service_type: "openrouter" | "ollama" | "custom";
  api_key?: string;
  model?: string;
  dialog_recorder_model?: string;
  base_url?: string;
  dialog_recorder_base_url?: string;
  embedding_model?: string;
  embedding_base_url?: string;
  num_ctx?: string;
}

export interface TestLLMConnectionParams {
  service_type: "openrouter" | "ollama" | "custom";
  api_key?: string | undefined;
  model?: string | undefined;
  base_url?: string | undefined;
  dialog_recorder_base_url?: string;
  num_ctx?: string;
  /** "embedding" probes the embeddings endpoint instead of chat. */
  probe?: "chat" | "embedding";
}

export interface OllamaCapabilitiesParams {
  service_type: "ollama";
  model: string;
  base_url?: string;
}

/**
 * Current active LLM configuration (no secrets). Only `configured: false`
 * is present when no integration is active; the remaining fields appear
 * only when their credential is stored.
 */
export const llmConfigMetaSchema = z.looseObject({
  configured: z.boolean(),
  service_type: z.enum(providerTypes).optional(),
  model: z.string().optional(),
  dialog_recorder_model: z.string().optional(),
  base_url: z.string().optional(),
  dialog_recorder_base_url: z.string().optional(),
  num_ctx: z.string().optional(),
});

export type LLMConfigMeta = z.infer<typeof llmConfigMetaSchema>;

export async function getLLMConfig(): Promise<LLMConfigMeta> {
  return apiRequest("/admin/llm/config", {}, llmConfigMetaSchema);
}

/** Response of PUT /admin/llm/config. */
export const llmConfigSaveResponseSchema = z.looseObject({
  detail: z.string(),
});

export async function saveLLMConfig(params: SaveLLMConfigParams): Promise<void> {
  await apiRequest(
    "/admin/llm/config",
    {
      method: "PUT",
      body: JSON.stringify(params),
    },
    llmConfigSaveResponseSchema
  );
}

/**
 * Models listed by the provider. `record` is present only for a custom
 * provider that does not support model listing.
 */
export const llmModelListResponseSchema = z.looseObject({
  models: z.array(z.string()),
  record: z.string().optional(),
});

export async function getModelList(params: {
  service_type: "openrouter" | "ollama" | "custom";
  api_key?: string | undefined;
  base_url?: string | undefined;
}) {
  return apiRequest(
    "/admin/llm/models",
    {
      method: "POST",
      body: JSON.stringify(params),
    },
    llmModelListResponseSchema
  );
}

/**
 * Provider connectivity probe. Success carries `provider` (plus
 * `dimension` for the embedding probe); failure carries `error`.
 */
export const testLLMConnectionResponseSchema = z.looseObject({
  ok: z.boolean(),
  provider: z.string().optional(),
  error: z.string().optional(),
  dimension: z.number().optional(),
});

export async function testLLMConnection(params: TestLLMConnectionParams) {
  return apiRequest(
    "/admin/llm/test",
    {
      method: "POST",
      body: JSON.stringify(params),
    },
    testLLMConnectionResponseSchema
  );
}

/**
 * Ollama runtime capabilities for one model. The optional fields are
 * present but null when the value could not be detected.
 */
export const ollamaCapabilitiesResponseSchema = z.looseObject({
  model: z.string(),
  source: z.string(),
  runtime_context_tokens: z.number(),
  model_max_context_tokens: z.number().nullable(),
  model_size_bytes: z.number().nullable(),
  size_vram_bytes: z.number().nullable(),
  kv_bytes_per_token: z.number().nullable(),
  estimated_runtime_kv_bytes: z.number().nullable(),
  estimated_runtime_kv_gib: z.number().nullable(),
});

export type OllamaCapabilitiesResponse = z.infer<
  typeof ollamaCapabilitiesResponseSchema
>;

export async function detectOllamaCapabilities(
  params: OllamaCapabilitiesParams
): Promise<OllamaCapabilitiesResponse> {
  return apiRequest(
    "/admin/llm/capabilities",
    {
      method: "POST",
      body: JSON.stringify(params),
    },
    ollamaCapabilitiesResponseSchema
  );
}

// --- Multi-config CRUD API ---

/**
 * LLM runtime reload result reported by the orchestrator. When the
 * orchestrator is unreachable (or the reload call fails) only `success`
 * and `error` are present.
 */
export const llmReloadResultSchema = z.looseObject({
  success: z.boolean(),
  active_provider: z.string().nullable().optional(),
  model: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
});

/**
 * One saved LLM configuration (no secrets). The credential fields are
 * present only when their credential is stored.
 */
export const llmConfigEntrySchema = z.looseObject({
  id: z.string(),
  service_type: z.enum(providerTypes),
  enabled: z.boolean(),
  created_at: z.string(),
  has_api_key: z.boolean(),
  model: z.string().optional(),
  dialog_recorder_model: z.string().optional(),
  base_url: z.string().optional(),
  dialog_recorder_base_url: z.string().optional(),
  embedding_model: z.string().optional(),
  embedding_base_url: z.string().optional(),
  num_ctx: z.string().optional(),
});

export type LLMConfigEntry = z.infer<typeof llmConfigEntrySchema>;

/** All saved LLM configurations. */
export const llmConfigsListResponseSchema = z.looseObject({
  configs: z.array(llmConfigEntrySchema),
});

export async function listLLMConfigs(): Promise<{ configs: LLMConfigEntry[] }> {
  return apiRequest("/admin/llm/configs", {}, llmConfigsListResponseSchema);
}

/** Response of POST /admin/llm/configs (201). */
export const createLLMConfigResponseSchema = z.looseObject({
  id: z.string(),
  detail: z.string(),
});

export async function createLLMConfig(
  params: SaveLLMConfigParams
): Promise<{ id: string; detail: string }> {
  return apiRequest(
    "/admin/llm/configs",
    {
      method: "POST",
      body: JSON.stringify(params),
    },
    createLLMConfigResponseSchema
  );
}

/** Response of PATCH /admin/llm/configs/{id}. */
export const llmConfigUpdateResponseSchema = z.looseObject({
  detail: z.string(),
  reload: llmReloadResultSchema,
});

export async function updateLLMConfig(
  id: string,
  params: Partial<SaveLLMConfigParams>
): Promise<void> {
  await apiRequest(
    `/admin/llm/configs/${encodePathSegment(id)}`,
    {
      method: "PATCH",
      body: JSON.stringify(params),
    },
    llmConfigUpdateResponseSchema
  );
}

export async function deleteLLMConfig(id: string): Promise<void> {
  await apiRequest(`/admin/llm/configs/${encodePathSegment(id)}`, { method: "DELETE" });
}

/** Response of POST /admin/llm/configs/{id}/activate. */
export const activateLLMConfigResponseSchema = z.looseObject({
  detail: z.string(),
  reload: llmReloadResultSchema,
});

export async function activateLLMConfig(id: string) {
  return apiRequest(
    `/admin/llm/configs/${encodePathSegment(id)}/activate`,
    {
      method: "POST",
    },
    activateLLMConfigResponseSchema
  );
}

/** Response of POST /admin/llm/configs/{id}/test. */
export const testSavedLLMConfigResponseSchema = z.looseObject({
  ok: z.boolean(),
  provider: z.string().optional(),
  error: z.string().optional(),
});

/**
 * Test a saved configuration with its stored credentials. Returns the
 * probe result with `provider`/`error` omitted when absent, matching the
 * shape the LLM config page stores in its per-config test results.
 */
export async function testSavedLLMConfig(id: string): Promise<{
  ok: boolean;
  provider?: string;
  error?: string;
}> {
  const result = await apiRequest(
    `/admin/llm/configs/${encodePathSegment(id)}/test`,
    {
      method: "POST",
    },
    testSavedLLMConfigResponseSchema
  );
  const out: { ok: boolean; provider?: string; error?: string } = {
    ok: result.ok,
  };
  if (result.provider !== undefined) {
    out.provider = result.provider;
  }
  if (result.error !== undefined) {
    out.error = result.error;
  }
  return out;
}
