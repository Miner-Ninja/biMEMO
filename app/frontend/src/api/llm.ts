import { apiRequest, encodePathSegment } from "@/lib/bimemo-api";

export interface LLMConfigMeta {
  configured: boolean;
  service_type?: "openrouter" | "ollama" | "custom";
  model?: string;
  dialog_recorder_model?: string;
  base_url?: string;
  dialog_recorder_base_url?: string;
  num_ctx?: string;
}

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
  api_key?: string;
  model?: string;
  base_url?: string;
  dialog_recorder_base_url?: string;
  num_ctx?: string;
}

export interface OllamaCapabilitiesParams {
  service_type: "ollama";
  model: string;
  base_url?: string;
}

export interface OllamaCapabilitiesResponse {
  model: string;
  source?: string;
  runtime_context_tokens?: number;
  model_max_context_tokens?: number | null;
  model_size_bytes?: number | null;
  size_vram_bytes?: number | null;
  kv_bytes_per_token?: number | null;
  estimated_runtime_kv_bytes?: number | null;
  estimated_runtime_kv_gib?: number | null;
  error?: string;
}

export async function getLLMConfig(): Promise<LLMConfigMeta> {
  return apiRequest<LLMConfigMeta>("/admin/llm/config");
}

export async function saveLLMConfig(params: SaveLLMConfigParams): Promise<void> {
  await apiRequest("/admin/llm/config", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function getModelList(params: {
  service_type: "openrouter" | "ollama" | "custom";
  api_key?: string;
  base_url?: string;
}): Promise<{ models: string[]; error?: string; record?: string }> {
  return apiRequest("/admin/llm/models", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function testLLMConnection(params: TestLLMConnectionParams): Promise<{
  ok: boolean;
  provider?: string;
  record?: string;
  error?: string;
}> {
  return apiRequest("/admin/llm/test", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function detectOllamaCapabilities(
  params: OllamaCapabilitiesParams
): Promise<OllamaCapabilitiesResponse> {
  return apiRequest("/admin/llm/capabilities", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

// --- Multi-config CRUD API ---

export type ProviderType = "openrouter" | "ollama" | "custom";

export interface LLMConfigEntry {
  id: string;
  service_type: ProviderType;
  model?: string;
  dialog_recorder_model?: string;
  base_url?: string;
  dialog_recorder_base_url?: string;
  embedding_model?: string;
  embedding_base_url?: string;
  num_ctx?: string;
  enabled: boolean;
  has_api_key: boolean;
  created_at: string;
}

export async function listLLMConfigs(): Promise<{ configs: LLMConfigEntry[] }> {
  return apiRequest("/admin/llm/configs");
}

export async function createLLMConfig(
  params: SaveLLMConfigParams
): Promise<{ id: string; detail: string }> {
  return apiRequest("/admin/llm/configs", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function updateLLMConfig(
  id: string,
  params: Partial<SaveLLMConfigParams>
): Promise<void> {
  await apiRequest(`/admin/llm/configs/${encodePathSegment(id)}`, {
    method: "PUT",
    body: JSON.stringify(params),
  });
}

export async function deleteLLMConfig(id: string): Promise<void> {
  await apiRequest(`/admin/llm/configs/${encodePathSegment(id)}`, { method: "DELETE" });
}

export async function activateLLMConfig(id: string): Promise<{
  detail: string;
  reload?: {
    success: boolean;
    active_provider?: string | null;
    model?: string | null;
    error?: string | null;
  };
}> {
  return apiRequest(`/admin/llm/configs/${encodePathSegment(id)}/activate`, {
    method: "POST",
  });
}

export async function testSavedLLMConfig(id: string): Promise<{
  ok: boolean;
  provider?: string;
  error?: string;
}> {
  return apiRequest(`/admin/llm/configs/${encodePathSegment(id)}/test`, {
    method: "POST",
  });
}
