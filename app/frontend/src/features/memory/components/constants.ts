import type {
  DiagnosticsView,
  MemoryDataView,
  MemoryDebugQuestionTab,
  MemoryDebugSection,
  ProcessingLogsView,
} from "@/features/memory/components/types";
import type { TranslationKey } from "@/i18n";

export const MEMORY_DEBUG_SECTIONS: Array<{
  id: MemoryDebugSection;
  labelKey: TranslationKey;
  descriptionKey: TranslationKey;
}> = [
  {
    id: "overview",
    labelKey: "memoryDebug.sectionOverview",
    descriptionKey: "memoryDebug.sectionOverviewDescription",
  },
  {
    id: "memory-data",
    labelKey: "memoryDebug.sectionMemoryData",
    descriptionKey: "memoryDebug.sectionMemoryDataDescription",
  },
  {
    id: "diagnostics",
    labelKey: "memoryDebug.sectionDiagnostics",
    descriptionKey: "memoryDebug.sectionDiagnosticsDescription",
  },
  {
    id: "processing-logs",
    labelKey: "memoryDebug.sectionProcessingLogs",
    descriptionKey: "memoryDebug.sectionProcessingLogsDescription",
  },
  {
    id: "assertions",
    labelKey: "memoryDebug.sectionAssertions",
    descriptionKey: "memoryDebug.sectionAssertionsDescription",
  },
];

export const MEMORY_DEBUG_QUESTION_TABS: Array<{
  id: MemoryDebugQuestionTab;
  labelKey: TranslationKey;
}> = [
  {
    id: "happening",
    labelKey: "memoryDebug.tabHappening",
  },
  {
    id: "remembered",
    labelKey: "memoryDebug.tabRemembered",
  },
  {
    id: "answers",
    labelKey: "memoryDebug.tabAnswers",
  },
  {
    id: "broken",
    labelKey: "memoryDebug.tabBroken",
  },
];

export const DIAGNOSTICS_VIEWS: Array<{
  id: DiagnosticsView;
  labelKey: TranslationKey;
  descriptionKey: TranslationKey;
}> = [
  {
    id: "routing",
    labelKey: "memoryDebug.viewRouting",
    descriptionKey: "memoryDebug.viewRoutingDescription",
  },
  {
    id: "quality",
    labelKey: "memoryDebug.viewQuality",
    descriptionKey: "memoryDebug.viewQualityDescription",
  },
  {
    id: "audit",
    labelKey: "memoryDebug.viewAudit",
    descriptionKey: "memoryDebug.viewAuditDescription",
  },
  {
    id: "extractor",
    labelKey: "memoryDebug.viewExtractor",
    descriptionKey: "memoryDebug.viewExtractorDescription",
  },
];

export const MEMORY_DATA_VIEWS: Array<{
  id: MemoryDataView;
  labelKey: TranslationKey;
  descriptionKey: TranslationKey;
}> = [
  {
    id: "context",
    labelKey: "memoryDebug.viewContext",
    descriptionKey: "memoryDebug.viewContextDescription",
  },
  {
    id: "timeline",
    labelKey: "memoryDebug.viewTimeline",
    descriptionKey: "memoryDebug.viewTimelineDescription",
  },
  {
    id: "links",
    labelKey: "memoryDebug.viewLinks",
    descriptionKey: "memoryDebug.viewLinksDescription",
  },
  {
    id: "files",
    labelKey: "memoryDebug.viewFiles",
    descriptionKey: "memoryDebug.viewFilesDescription",
  },
  {
    id: "queue",
    labelKey: "memoryDebug.viewQueue",
    descriptionKey: "memoryDebug.viewQueueDescription",
  },
  {
    id: "entities",
    labelKey: "memoryDebug.viewEntities",
    descriptionKey: "memoryDebug.viewEntitiesDescription",
  },
];

export const PROCESSING_LOG_VIEWS: Array<{
  id: ProcessingLogsView;
  labelKey: TranslationKey;
  descriptionKey: TranslationKey;
}> = [
  {
    id: "records-log",
    labelKey: "memoryDebug.viewRecordsLog",
    descriptionKey: "memoryDebug.viewRecordsLogDescription",
  },
];

export const TYPE_COLORS: Record<string, string> = {
  user: "bg-sky-500/10 text-sky-400 border-sky-500/20",
  assistant: "bg-green-500/10 text-green-400 border-green-500/20",
  message: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  record: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  assertion: "bg-violet-500/10 text-violet-400 border-violet-500/20",
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

export const EXTRACTOR_DECISION_COLORS: Record<string, string> = {
  assertion_stored: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  assertion_invalidated_previous: "bg-sky-500/10 text-sky-400 border-sky-500/20",
  assertion_conflict_kept_both: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  assertion_skipped_duplicate: "bg-muted text-muted-foreground border-border",
  assertion_skipped_subject_undeclared:
    "bg-orange-500/10 text-orange-400 border-orange-500/20",
  entity_skipped_forbidden_name:
    "bg-orange-500/10 text-orange-400 border-orange-500/20",
  extraction_empty_or_unparsed: "bg-red-500/10 text-red-400 border-red-500/20",
  assertion_skipped_unsafe_output: "bg-red-500/10 text-red-400 border-red-500/20",
};

export const SORT_OPTIONS: ReadonlyArray<{
  value: "desc" | "asc";
  labelKey: TranslationKey;
}> = [
  { value: "desc", labelKey: "memoryDebug.sortNewestFirst" },
  { value: "asc", labelKey: "memoryDebug.sortOldestFirst" },
] as const;
