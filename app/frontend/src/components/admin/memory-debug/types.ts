export type MemoryDebugSection =
  "overview" | "diagnostics" | "memory-data" | "processing-logs" | "assertions";

export type DiagnosticsView = "routing" | "quality" | "audit";

export type MemoryDataView = "context" | "timeline" | "links" | "files";

export type ProcessingLogsView = "records-log";

export interface PipelineData {
  messages: number;
  records: number;
  assertions: number;
}

export interface ContextTierData {
  count: number;
  entries: Array<Record<string, unknown>>;
}

export interface ContextData {
  tiers: Record<string, ContextTierData>;
}

export interface QualityData {
  counts: {
    message: number;
    record: number;
    assertion: number;
  };
  assertions_valid: number;
  assertions_invalidated: number;
  assertions_without_evidence: number;
  assertions_with_missing_evidence: number;
  duplicate_assertion_texts: number;
  orphan_entities: number;
  quality_score: number;
}

export interface RouteDiagnosticsSummaryData {
  totals: {
    total_rows: number;
    fallback_rows: number;
    mismatch_rows: number;
    long_term_rows: number;
  };
  recent_hours: number | null;
  mismatch_labels: Array<{ label: string; count: number }>;
  final_intents: Array<{ intent: string; count: number }>;
  fallback_reasons: Array<{ reason: string; count: number }>;
}

export interface RouteDiagnosticEntry {
  id: number;
  created_at: string;
  session_id: string;
  conversation_id: string;
  message_id: string;
  channel: string;
  initial_route: {
    intent: string;
    confidence: string;
    role_filter: string | null;
    source: string;
    signal_groups: string[];
  };
  fallback: {
    attempted: boolean;
    reason: string | null;
    intent: string | null;
    confidence: string | null;
    role_filter: string | null;
    source: string | null;
  };
  final_route: {
    intent: string;
    confidence: string;
    role_filter: string | null;
    source: string;
    signal_groups: string[];
  };
  route_mismatch: boolean;
  mismatch_label: string;
  used_long_term_mode: boolean;
}

export interface TimelineEntry {
  type: string;
  id: string;
  content: string | Record<string, unknown>[];
  created_at: string;
  meta: Record<string, unknown>;
}

export interface TimelineData {
  entries: TimelineEntry[];
  total_count: number;
}

export interface FileContextIndexEntry {
  id: string;
  original_name: string;
  size: number;
  mime_type: string;
  created_at: string;
}

export interface FileContextChunkData {
  chunk_id: string;
  chunk_index: number;
  text: string;
  line_start: number | null;
  line_end: number | null;
  estimated_tokens: number;
}

export interface FileContextUsageData {
  processing_mode: string;
  mode_reason: string;
  user_query: string;
  selected_chunk_ids: string[];
  selected_chunk_indexes: number[];
  used_at: string;
}

export interface FileContextData {
  file_id: string;
  file_kind: string;
  estimated_tokens: number;
  processing_mode: string;
  processing_reason: string;
  summary_overview: string;
  summary_structure: string[];
  summary_key_points: string[];
  summary_warnings: string[];
  chunks: FileContextChunkData[];
  last_usage: FileContextUsageData | null;
  prompt_blocks: string[];
}

export interface AuditEntry {
  id: number;
  timestamp: number;
  event_type: string;
  tool_name: string | null;
  parameters: string | null;
  result: string | null;
  zone: string | null;
  duration_ms: number | null;
}

export interface RecordsLogEntry {
  timestamp: string;
  decision: string;
  conversation_id: string;
  session_id: string;
  source_message_ids: string[];
  messages: string[];
  message_count: number;
  prompt?: Array<{ role: string; content: string }>;
  raw_output?: string;
  summary?: string;
  key_facts?: string[];
  action_items?: string[];
  memory_kinds?: string[];
  record_id?: string;
  model?: string;
  error?: string;
  detail?: string;
}

export interface SummariesLogRecord {
  summary: string;
  key_facts: string[];
  action_items: string[];
  memory_kinds?: string[];
}

export interface SummariesLogEntry {
  timestamp: string;
  decision: string;
  model?: string;
  record_count: number;
  source_record_ids: string[];
  records: SummariesLogRecord[];
  prompt?: Array<{ role: string; content: string }>;
  raw_output?: string;
  main_point?: string;
  based_on?: string[];
  trust_level?: string;
  scope?: string;
  record_relations?: string[];
  summary_id?: string;
  error?: string;
  detail?: string;
}
