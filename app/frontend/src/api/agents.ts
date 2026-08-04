import { apiRequest } from "@/lib/bimemo-api";

export interface AgentTokenEntry {
  label: string;
  capability: string;
  created_at: string;
}

export interface IssueAgentTokenResponse {
  label: string;
  token: string;
}

export interface CaRootResponse {
  certificate: string;
}

export interface ConnectionConfigResponse {
  url: string;
  label: string;
}

export async function listAgentTokens(): Promise<AgentTokenEntry[]> {
  return apiRequest<AgentTokenEntry[]>("/admin/agents/list");
}

export async function issueAgentToken(
  label: string,
  capability: string = "read",
  reauthToken?: string
): Promise<IssueAgentTokenResponse> {
  const headers: Record<string, string> = {};
  if (reauthToken) {
    headers["X-Reauth-Token"] = reauthToken;
  }
  const query = new URLSearchParams({ label, capability }).toString();
  return apiRequest<IssueAgentTokenResponse>(`/admin/agents/issue?${query}`, {
    method: "POST",
    headers,
  });
}

export async function revokeAgentToken(
  label: string,
  reauthToken?: string
): Promise<{ detail: string }> {
  const headers: Record<string, string> = {};
  if (reauthToken) {
    headers["X-Reauth-Token"] = reauthToken;
  }
  return apiRequest<{ detail: string }>(`/admin/agents/revoke/${label}`, {
    method: "DELETE",
    headers,
  });
}

export async function downloadCaRoot(): Promise<CaRootResponse> {
  return apiRequest<CaRootResponse>("/admin/agents/ca-root");
}

export async function getConnectionConfig(
  label: string
): Promise<ConnectionConfigResponse> {
  const query = new URLSearchParams({ label }).toString();
  return apiRequest<ConnectionConfigResponse>(`/admin/agents/config-block?${query}`);
}
