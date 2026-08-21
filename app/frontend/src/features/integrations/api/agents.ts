import { z } from "zod";

import { apiRequest } from "@/services/bimemo-api";

/** One configured MCP agent token (metadata only; never the plaintext token). */
export const agentTokenEntrySchema = z.looseObject({
  label: z.string(),
  capability: z.string(),
  created_at: z.string(),
});

export type AgentTokenEntry = z.infer<typeof agentTokenEntrySchema>;

/** Body of GET /admin/agents/list: the list of agent token entries. */
export const agentTokenListSchema = z.array(agentTokenEntrySchema);

/** Body of POST /admin/agents/issue: the label and plaintext token. */
export const issueAgentTokenResponseSchema = z.looseObject({
  label: z.string(),
  token: z.string(),
});

export type IssueAgentTokenResponse = z.infer<typeof issueAgentTokenResponseSchema>;

/** Body of GET /admin/agents/ca-root: the public CA root certificate PEM. */
export const caRootResponseSchema = z.looseObject({
  certificate: z.string(),
});

export type CaRootResponse = z.infer<typeof caRootResponseSchema>;

/** Body of GET /admin/agents/config-block: the TLS MCP URL and token label. */
export const connectionConfigResponseSchema = z.looseObject({
  url: z.string(),
  label: z.string(),
});

export type ConnectionConfigResponse = z.infer<typeof connectionConfigResponseSchema>;

export async function listAgentTokens(): Promise<AgentTokenEntry[]> {
  return apiRequest("/admin/agents/list", {}, agentTokenListSchema);
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
  return apiRequest(
    `/admin/agents/issue?${query}`,
    {
      method: "POST",
      headers,
    },
    issueAgentTokenResponseSchema
  );
}

export async function revokeAgentToken(
  label: string,
  reauthToken?: string
): Promise<void> {
  const headers: Record<string, string> = {};
  if (reauthToken) {
    headers["X-Reauth-Token"] = reauthToken;
  }
  await apiRequest(`/admin/agents/revoke/${label}`, {
    method: "DELETE",
    headers,
  });
}

export async function downloadCaRoot(): Promise<CaRootResponse> {
  return apiRequest("/admin/agents/ca-root", {}, caRootResponseSchema);
}

export async function getConnectionConfig(
  label: string
): Promise<ConnectionConfigResponse> {
  const query = new URLSearchParams({ label }).toString();
  return apiRequest(
    `/admin/agents/config-block?${query}`,
    {},
    connectionConfigResponseSchema
  );
}
