/**
 * Token statistics types for chat requests.
 * Mirrors backend ExtendedUsage schema.
 */

/** Context window usage statistics */
export interface ContextUsage {
  /** Total context window size */
  total: number;
  /** Tokens used in context */
  used: number;
  /** Tokens available in context */
  free: number;
  /** Percentage of context used */
  used_percent: number;
}

/** One labelled processing-phase duration */
export interface StageTiming {
  /** Phase label (e.g. "Memory recall", "Web search", "Generation") */
  label: string;
  /** Phase duration (ms) */
  duration_ms: number;
}

/** Timing statistics for request processing */
export interface TimingStats {
  /** Time to process prompt (ms) */
  prompt_ms: number;
  /** Time to generate response (ms) */
  generation_ms: number;
  /** Total request time (ms) */
  total_ms: number;
  /** Ordered per-phase breakdown (empty on older messages) */
  stages?: StageTiming[];
}

/** Token budget and usage for one memory zone */
export interface ZoneBudgetItem {
  budget: number;
  used: number;
  label: string;
}

/** Memory zone budget breakdown */
export interface MemoryZones {
  working_memory: ZoneBudgetItem;
  recall_results: ZoneBudgetItem;
  conversation_summary: ZoneBudgetItem;
  total_available: number;
}

/** Extended usage statistics with context and timing */
export interface ExtendedUsage {
  /** Input tokens (prompt) */
  prompt_tokens: number;
  /** Output tokens (completion) */
  completion_tokens: number;
  /** Total tokens */
  total_tokens: number;
  /** Context window usage, when provider exposes it */
  context?: ContextUsage;
  /** Memory zone budget breakdown */
  memory_zones?: MemoryZones;
  /** Timing statistics */
  timing: TimingStats;
  /** Model name */
  model: string;
  /** Number of ReAct iterations */
  iterations: number;
}
