import type { ExtendedUsage } from "./stats";

/**
 * Query result containing SQL execution results
 */
export interface QueryResult {
  /** Column names in order */
  columns: string[];
  /** Row data as array of objects */
  data: Record<string, unknown>[];
  /** Total number of rows returned */
  rowCount: number;
}

/**
 * Tool execution status
 */
export interface ToolStatus {
  /** Name of the tool being executed */
  name: string;
  /** Execution status */
  status: "started" | "completed" | "error";
  /** Execution duration in milliseconds */
  duration_ms?: number;
}

/**
 * Human-readable labels for known tool names
 */
export const TOOL_LABELS: Record<string, string> = {
  runQuery: "Running query...",
  askDocs: "Searching documents...",
  compareDocs: "Comparing documents...",
};

/**
 * A single processing stage displayed during message handling
 */
export interface ProcessingStage {
  /** Unique identifier for this stage instance */
  id: string;
  /** Display label (e.g. "Thinking...", "Running query...") */
  label: string;
  /** Stage status */
  status: "active" | "completed" | "error";
  /** When this stage started (performance.now()) */
  startedAt: number;
  /** Frozen duration in ms (set when stage completes) */
  frozenDurationMs?: number;
}

/**
 * A single message in a conversation
 */
export interface Message {
  /** Unique identifier (UUID v4) */
  id: string;
  /** Who sent this message */
  role: "user" | "assistant";
  /** Message text content */
  content: string;
  /** Base64 data URLs for attached images */
  images?: string[];
  /** Whether this message originated from a voice recording */
  isVoice?: boolean;
  /** Base64 data URL of the recorded audio for playback */
  voiceDataUrl?: string;
  /** LLM thinking content */
  thinking?: string;
  /** Current tool execution status */
  toolStatus?: ToolStatus;
  /** Generated SQL query (assistant messages only) */
  sql?: string;
  /** Follow-up suggestions shown after the final answer */
  suggestions?: string[];
  /** Query execution results (assistant messages only) */
  results?: QueryResult;
  /** Error message if query failed (assistant messages only) */
  error?: string;
  /** Structured error category from backend */
  errorCategory?: string;
  /** Technical error detail for collapsible block */
  errorDetail?: string;
  /** User-facing suggestion for error recovery */
  errorSuggestion?: string;
  /** Processing stage timings (assistant messages only) */
  stageTimes?: { label: string; durationMs: number }[];
  /** Token usage statistics (assistant messages only) */
  stats?: ExtendedUsage;
  /** Pending RED zone actions awaiting user confirmation */
  pendingConfirmations?: PendingConfirmation[];
  /** When the message was created */
  timestamp: Date;
}

/**
 * A RED zone tool action awaiting user approval
 */
export interface PendingConfirmation {
  action_id: string;
  tool_name: string;
  parameters: Record<string, unknown>;
  status: "pending" | "approved" | "denied" | "expired";
}

/**
 * Per-conversation display settings
 */
export interface ChatSettings {
  /** Show SQL query blocks in assistant messages */
  showSql: boolean;
  /** Show LLM thinking blocks in assistant messages */
  showThinking: boolean;
}

/**
 * Default chat settings for new conversations
 */
export const DEFAULT_CHAT_SETTINGS: ChatSettings = {
  showSql: false,
  showThinking: false,
};

/**
 * A conversation session containing multiple messages
 */
export interface Chat {
  /** Unique identifier (UUID v4) */
  id: string;
  /** Display title, auto-generated from first user message */
  title: string;
  /** Active external bridge provider for this chat, if connected */
  bridgeProvider?: string | null;
  /** Per-conversation display settings */
  settings: ChatSettings;
  /** Current server-side processing status for this conversation */
  processingStatus: "idle" | "active";
  /** Human-readable server-side processing stage label */
  processingStage: string;
  /** When server-side processing started */
  processingStartedAt?: Date;
  /** Ordered list of messages in this conversation */
  messages: Message[];
  /** When the chat was created */
  createdAt: Date;
  /** When the chat was last updated (new message added) */
  updatedAt: Date;
}

/**
 * User's visual preference
 */
export type Theme = "dark" | "light";
