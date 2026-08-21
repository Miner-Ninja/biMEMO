/**
 * Shared server-state helpers for the query cache: the canonical
 * query keys (one cache entry per server resource) and the closed
 * error → display-text mapping.
 */
import type { TFunction } from "i18next";
import { normalizeApiError } from "@/services/bimemo-api";

/**
 * Canonical query keys. A parameterized resource (an entity, a
 * filtered list) is a function of its parameters; a fixed resource
 * is a constant prefix. Two components requesting the same resource
 * with the same parameters share one cache entry and one request.
 */
export const QUERY_KEYS = {
  integrations: ["/admin/integrations"],
  llmConfig: ["llm-config"],
  profile: ["profile"],
  memory: (query: string) => ["memory", query],
  memoryEntity: (id: string) => ["memory-entity", id],
  scheduledTasks: (filter: string) => ["scheduled-tasks", filter],
  skills: ["skills"],
  skill: (name: string) => ["skill", name],
  traces: ["traces"],
  trace: (id: string) => ["trace", id],
  entityDetail: (id: string) => ["memory-debug-entity", id],
  agentTokens: ["agent-tokens"],
  caRoot: ["ca-root"],
} as const;

/**
 * The single closed mapping from a failure to user-facing text.
 * No page may write its own error sentence: http errors show the
 * server's own message (falling back to a generic phrase when the
 * response carried none); contract and network failures show a
 * fixed, internationalized phrase.
 */
export function errorToDisplayText(err: unknown, t: TFunction): string {
  const n = normalizeApiError(err);
  if (n.kind === "contract") {
    return t("errors.contract");
  }
  if (n.kind === "network") {
    return t("errors.network");
  }
  return n.message || t("errors.http");
}
