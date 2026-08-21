/**
 * Web API for the user-facing Memory page.
 *
 * Reads go through the admin memory routes (Socket B on the backend).
 * The single write — assertion invalidation — is a destructive action:
 * it carries a one-time re-auth token and is forwarded by the backend
 * to the orchestrator (Socket A) over IPC.
 */

import { z } from "zod";

import { apiRequest } from "@/services/bimemo-api";

export type InvalidateReason = "forget" | "incorrect";

/**
 * One graph entity row. `aliases` is the parsed JSON alias list;
 * `active_assertions` is only present on the paged list (the
 * search and single-entity responses omit it).
 */
export const memoryEntitySchema = z.looseObject({
  id: z.string(),
  entity_type: z.string(),
  canonical_name: z.string(),
  aliases: z.array(z.string()),
  created_at: z.string(),
  active_assertions: z.number().optional(),
});

export type MemoryEntity = z.infer<typeof memoryEntitySchema>;

/** Paged entity list, newest first, with the total entity count. */
export const listMemoryEntitiesResponseSchema = z.looseObject({
  entities: z.array(memoryEntitySchema),
  total: z.number(),
});

export type ListMemoryEntitiesResponse = z.infer<
  typeof listMemoryEntitiesResponseSchema
>;

export async function listMemoryEntities(
  limit = 100
): Promise<ListMemoryEntitiesResponse> {
  return apiRequest(
    `/admin/memory/graph/entities?limit=${limit}`,
    undefined,
    listMemoryEntitiesResponseSchema
  );
}

/** Name/alias search over entities; no total and no per-entity counts. */
export const searchMemoryEntitiesResponseSchema = z.looseObject({
  entities: z.array(memoryEntitySchema),
});

export type SearchMemoryEntitiesResponse = z.infer<
  typeof searchMemoryEntitiesResponseSchema
>;

export async function searchMemoryEntities(
  query: string,
  limit = 50
): Promise<SearchMemoryEntitiesResponse> {
  const params = new URLSearchParams({ query, limit: String(limit) });
  return apiRequest(
    `/admin/memory/graph/entities?${params.toString()}`,
    undefined,
    searchMemoryEntitiesResponseSchema
  );
}

/**
 * One assertion edge. `object_entity_id` and `object_value` are mutually
 * exclusive in storage (CHECK constraint); `valid_from`, `invalid_from`,
 * and `invalidated_by_assertion_id` stay null until set. `direction`
 * labels which side of the edge the requested entity sits on.
 */
export const memoryAssertionSchema = z.looseObject({
  id: z.string(),
  subject_entity_id: z.string(),
  relation: z.string(),
  object_entity_id: z.string().nullable(),
  object_value: z.string().nullable(),
  assertion_text: z.string(),
  trust_level: z.string(),
  assertion_scope: z.string(),
  valid_from: z.string().nullable(),
  invalid_from: z.string().nullable(),
  invalidated_by_assertion_id: z.string().nullable(),
  source_record_ids: z.array(z.string()),
  created_at: z.string(),
  direction: z.enum(["subject", "object"]),
});

export type MemoryAssertion = z.infer<typeof memoryAssertionSchema>;

/** A neighbor entity resolved for an assertion edge (id + label + type). */
export const memoryRelatedEntitySchema = z.looseObject({
  id: z.string(),
  canonical_name: z.string(),
  entity_type: z.string(),
});

export type MemoryRelatedEntity = z.infer<typeof memoryRelatedEntitySchema>;

/** One entity, its assertions, and its resolved neighbors keyed by id. */
export const getMemoryEntityResponseSchema = z.looseObject({
  entity: memoryEntitySchema,
  assertions: z.array(memoryAssertionSchema),
  related: z.record(z.string(), memoryRelatedEntitySchema),
});

export type GetMemoryEntityResponse = z.infer<typeof getMemoryEntityResponseSchema>;

export async function getMemoryEntity(
  entityId: string
): Promise<GetMemoryEntityResponse> {
  return apiRequest(
    `/admin/memory/graph/entities/${encodeURIComponent(entityId)}`,
    undefined,
    getMemoryEntityResponseSchema
  );
}

/** One stored record resolved to its conversation reference. */
export const memoryRecordRefSchema = z.looseObject({
  id: z.string(),
  conversation_id: z.string(),
  created_at: z.string(),
  summary: z.string(),
});

export type MemoryRecordRef = z.infer<typeof memoryRecordRefSchema>;

export const resolveMemoryRecordResponseSchema = z.looseObject({
  record: memoryRecordRefSchema,
});

export type ResolveMemoryRecordResponse = z.infer<
  typeof resolveMemoryRecordResponseSchema
>;

export async function resolveMemoryRecord(
  recordId: string
): Promise<ResolveMemoryRecordResponse> {
  return apiRequest(
    `/admin/memory/records/${encodeURIComponent(recordId)}`,
    undefined,
    resolveMemoryRecordResponseSchema
  );
}

/** Invalidation result; false for unknown or already-invalid ids. */
export const invalidateMemoryAssertionResponseSchema = z.looseObject({
  invalidated: z.boolean(),
});

export type InvalidateMemoryAssertionResponse = z.infer<
  typeof invalidateMemoryAssertionResponseSchema
>;

export async function invalidateMemoryAssertion(
  assertionId: string,
  reason: InvalidateReason,
  reauthToken?: string
): Promise<InvalidateMemoryAssertionResponse> {
  const headers: Record<string, string> = {};
  if (reauthToken) {
    headers["X-Reauth-Token"] = reauthToken;
  }
  const params = new URLSearchParams({ reason }).toString();
  return apiRequest(
    `/admin/memory/assertions/${encodeURIComponent(assertionId)}/invalidate?${params}`,
    { method: "POST", headers },
    invalidateMemoryAssertionResponseSchema
  );
}
