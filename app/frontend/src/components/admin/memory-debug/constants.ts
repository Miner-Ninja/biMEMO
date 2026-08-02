import type {
  DiagnosticsView,
  MemoryDataView,
  MemoryDebugSection,
  ProcessingLogsView,
} from "@/components/admin/memory-debug/types";

export const MEMORY_DEBUG_SECTIONS: Array<{
  id: MemoryDebugSection;
  label: string;
  description: string;
}> = [
  {
    id: "overview",
    label: "Overview",
    description: "Current memory state and the main things that need attention.",
  },
  {
    id: "memory-data",
    label: "Memory data",
    description: "Stored messages, records, summaries, and how they connect.",
  },
  {
    id: "diagnostics",
    label: "Diagnostics",
    description: "Why the system made its decisions and where drift appears.",
  },
  {
    id: "processing-logs",
    label: "Processing logs",
    description: "Detailed record and summary processing records.",
  },
  {
    id: "summaries",
    label: "Summaries",
    description: "Learned main points and insights extracted from conversations.",
  },
];

export const DIAGNOSTICS_VIEWS: Array<{
  id: DiagnosticsView;
  label: string;
  description: string;
}> = [
  {
    id: "routing",
    label: "Routing",
    description:
      "How memory questions were classified and when the backup classifier intervened.",
  },
  {
    id: "quality",
    label: "Quality",
    description:
      "Coverage, missing links, and other signals that lower memory reliability.",
  },
  {
    id: "audit",
    label: "Audit",
    description:
      "Recent system events, tool calls, and technical results tied to memory behavior.",
  },
];

export const MEMORY_DATA_VIEWS: Array<{
  id: MemoryDataView;
  label: string;
  description: string;
}> = [
  {
    id: "context",
    label: "Context snapshot",
    description: "What the system is currently pulling into memory context.",
  },
  {
    id: "timeline",
    label: "Timeline",
    description: "Messages, records, and summaries arranged in time order.",
  },
  {
    id: "links",
    label: "Memory links",
    description: "How messages, records, and summaries connect to each other.",
  },
  {
    id: "files",
    label: "File context",
    description:
      "Stored summaries and chunks for attached files that were too large to inline safely.",
  },
];

export const PROCESSING_LOG_VIEWS: Array<{
  id: ProcessingLogsView;
  label: string;
  description: string;
}> = [
  {
    id: "records-log",
    label: "Records log",
    description: "Detailed records for record creation, skips, and failures.",
  },
  {
    id: "summaries-log",
    label: "Summaries log",
    description: "Detailed records for summary creation, skips, and failures.",
  },
];

export const TYPE_COLORS: Record<string, string> = {
  user: "bg-sky-500/10 text-sky-400 border-sky-500/20",
  assistant: "bg-green-500/10 text-green-400 border-green-500/20",
  message: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  record: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  summary: "bg-violet-500/10 text-violet-400 border-violet-500/20",
  file: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
};

export const ROUTE_MISMATCH_COLORS: Record<string, string> = {
  no_mismatch: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  intent_changed: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  role_filter_changed: "bg-sky-500/10 text-sky-400 border-sky-500/20",
  long_term_flag_changed: "bg-violet-500/10 text-violet-400 border-violet-500/20",
  source_changed: "bg-orange-500/10 text-orange-400 border-orange-500/20",
};

export const SUMMARY_STATUS_COLORS: Record<string, string> = {
  fresh: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  stale: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  partial_support: "bg-orange-500/10 text-orange-400 border-orange-500/20",
  orphaned: "bg-red-500/10 text-red-400 border-red-500/20",
};

export const NOTE_RELATION_COLORS: Record<string, string> = {
  supports: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  conflicts: "bg-red-500/10 text-red-400 border-red-500/20",
  outdates: "bg-sky-500/10 text-sky-400 border-sky-500/20",
};

export const SUMMARY_SCOPE_COLORS: Record<string, string> = {
  long_term: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  short_term: "bg-sky-500/10 text-sky-400 border-sky-500/20",
};

export const AUDIT_EVENT_COLORS: Record<string, string> = {
  tool_call: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  tool_result: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
  security: "bg-red-500/10 text-red-400 border-red-500/20",
  injection: "bg-red-500/10 text-red-400 border-red-500/20",
  llm_call: "bg-violet-500/10 text-violet-400 border-violet-500/20",
  dialog_recorder: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  distiller: "bg-pink-500/10 text-pink-400 border-pink-500/20",
};

export const NOTES_LOG_COLORS: Record<string, string> = {
  stored: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  duplicate_memory: "bg-sky-500/10 text-sky-400 border-sky-500/20",
  not_memory_worthy: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  unsafe_output: "bg-red-500/10 text-red-400 border-red-500/20",
  parse_failed: "bg-red-500/10 text-red-400 border-red-500/20",
  llm_error: "bg-red-500/10 text-red-400 border-red-500/20",
  rate_limited: "bg-orange-500/10 text-orange-400 border-orange-500/20",
};

export const SUMMARIES_LOG_COLORS: Record<string, string> = {
  stored: "bg-violet-500/10 text-violet-400 border-violet-500/20",
  no_observations: "bg-muted text-muted-foreground border-border",
  parse_failed: "bg-red-500/10 text-red-400 border-red-500/20",
  llm_error: "bg-red-500/10 text-red-400 border-red-500/20",
  unsafe_output: "bg-red-500/10 text-red-400 border-red-500/20",
};

export const SORT_OPTIONS = [
  { value: "desc", label: "Newest first" },
  { value: "asc", label: "Oldest first" },
] as const;
