import type { TFunction } from "i18next";
import type { TranslationKey } from "@/i18n";

/**
 * Human phrase for one decision code. `raw` marks a code the dictionary
 * does not know — callers render it as-is in a monospace face instead
 * of showing an empty label (contract R5).
 */
export interface HumanizedCode {
  text: string;
  raw?: true;
}

function humanize(
  t: TFunction,
  map: Record<string, TranslationKey>,
  code: string
): HumanizedCode {
  const key = map[code];
  if (key) return { text: t(key) };
  return { text: code, raw: true };
}

const ROUTE_INTENT_KEYS: Record<string, TranslationKey> = {
  non_memory_question: "memoryDebug.phraseNonMemoryQuestion",
  memory_question: "memoryDebug.phraseMemoryQuestion",
  evidence_question: "memoryDebug.phraseEvidenceQuestion",
};

const MISMATCH_LABEL_KEYS: Record<string, TranslationKey> = {
  no_mismatch: "memoryDebug.phraseNoMismatch",
  intent_changed: "memoryDebug.phraseIntentChanged",
  role_filter_changed: "memoryDebug.phraseRoleFilterChanged",
  long_term_flag_changed: "memoryDebug.phraseLongTermFlagChanged",
  source_changed: "memoryDebug.phraseSourceChanged",
};

const RECORDER_DECISION_KEYS: Record<string, TranslationKey> = {
  stored: "memoryDebug.phraseRecStored",
  record_stored_by_llm_memory_judgment: "memoryDebug.phraseRecStored",
  duplicate_memory: "memoryDebug.phraseRecDuplicate",
  not_memory_worthy: "memoryDebug.phraseRecNotWorthy",
  unsafe_output: "memoryDebug.phraseRecUnsafe",
  parse_failed: "memoryDebug.phraseRecParseFailed",
  llm_error: "memoryDebug.phraseRecLlmError",
  rate_limited: "memoryDebug.phraseRecRateLimited",
};

export function humanizeRouteIntent(t: TFunction, code: string): HumanizedCode {
  return humanize(t, ROUTE_INTENT_KEYS, code);
}

export function humanizeMismatchLabel(t: TFunction, code: string): HumanizedCode {
  return humanize(t, MISMATCH_LABEL_KEYS, code);
}

export function humanizeRecorderDecision(t: TFunction, code: string): HumanizedCode {
  return humanize(t, RECORDER_DECISION_KEYS, code);
}
