/**
 * Web API for the user-facing Memory page.
 *
 * Reads go through the admin memory routes (Socket B on the backend).
 * The single write — assertion invalidation — is a destructive action:
 * it carries a one-time re-auth token and is forwarded by the backend
 * to the orchestrator (Socket A) over IPC.
 */

import { apiRequest } from "@/lib/bimemo-api";

export interface MemoryEntity {
  id: string;
  entity_type: string;
  canonical_name: string;
  aliases: string[];
  created_at: string;
  active_assertions?: number;
}

export interface MemoryAssertion {
  id: string;
  subject_entity_id: string;
  relation: string;
  object_entity_id: string | null;
  object_value: string | null;
  assertion_text: string;
  trust_level: string;
  assertion_scope: string;
  valid_from: string | null;
  invalid_from: string | null;
  invalidated_by_assertion_id: string | null;
  source_record_ids: string[];
  created_at: string;
  direction?: "subject" | "object";
}

export interface MemoryRecordRef {
  id: string;
  conversation_id: string | null;
  created_at: string | null;
  summary: string | null;
}

export type InvalidateReason = "forget" | "incorrect";

export async function listMemoryEntities(
  limit = 100
): Promise<{ entities: MemoryEntity[]; total?: number }> {
  return apiRequest(`/admin/memory/graph/entities?limit=${limit}`);
}

export async function searchMemoryEntities(
  query: string,
  limit = 50
): Promise<{ entities: MemoryEntity[] }> {
  const params = new URLSearchParams({ query, limit: String(limit) });
  return apiRequest(`/admin/memory/graph/entities?${params.toString()}`);
}

export async function getMemoryEntity(
  entityId: string
): Promise<{ entity: MemoryEntity; assertions: MemoryAssertion[] }> {
  return apiRequest(`/admin/memory/graph/entities/${encodeURIComponent(entityId)}`);
}

export async function resolveMemoryRecord(
  recordId: string
): Promise<{ record: MemoryRecordRef }> {
  return apiRequest(`/admin/memory/records/${encodeURIComponent(recordId)}`);
}

export async function invalidateMemoryAssertion(
  assertionId: string,
  reason: InvalidateReason,
  reauthToken?: string
): Promise<{ invalidated: boolean }> {
  const headers: Record<string, string> = {};
  if (reauthToken) {
    headers["X-Reauth-Token"] = reauthToken;
  }
  const params = new URLSearchParams({ reason }).toString();
  return apiRequest(
    `/admin/memory/assertions/${encodeURIComponent(assertionId)}/invalidate?${params}`,
    { method: "POST", headers }
  );
}
