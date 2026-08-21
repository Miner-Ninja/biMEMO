import type { ExtendedUsage } from "./stats";

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
  images?: string[] | undefined;
  /** Whether this message originated from a voice recording */
  isVoice?: boolean;
  /** Base64 data URL of the recorded audio for playback */
  voiceDataUrl?: string;
  /** LLM thinking content */
  thinking?: string | undefined;
  /** Current tool execution status */
  toolStatus?: ToolStatus;
  /** Follow-up suggestions shown after the final answer */
  suggestions?: string[] | undefined;
  /** Error message if query failed (assistant messages only) */
  error?: string | undefined;
  /** Structured error category from backend */
  errorCategory?: string;
  /** Technical error detail for collapsible block */
  errorDetail?: string;
  /** User-facing suggestion for error recovery */
  errorSuggestion?: string;
  /** Token usage statistics (assistant messages only) */
  stats?: ExtendedUsage | undefined;
  /** Pending RED zone actions awaiting user confirmation */
  pendingConfirmations?: PendingConfirmation[] | undefined;
  /** Typed value requested to resume the same backend run */
  pendingInput?: PendingInput | undefined;
  /** Used tools recorded for this message */
  usedTools?: UsedTool[] | undefined;
  /** When the message was created */
  timestamp: Date;
}

/**
 * A tool execution record
 */
export interface UsedTool {
  name: string;
  permission_mode: string;
  authorization_source: string;
  duration_ms?: number | null;
  success: boolean;
}

/**
 * A RED zone tool action awaiting user approval
 */
export interface PendingConfirmation {
  action_id: string;
  tool_name: string;
  parameters: Record<string, unknown>;
  status: "pending" | "approved" | "denied" | "expired";
  /** Server-derived: the tool's policy mode can carry a reusable grant. */
  grant_capable?: boolean;
  /** Server-derived host of the tool's url argument; null for non-URL tools. */
  url_host?: string | null;
}

export type ConfirmationScope = "once" | "domain" | "conversation";

export interface PendingInput {
  run_id: string;
  pending_input_id: string;
  question: string;
  expected_input_schema: Record<string, unknown>;
  expires_at: string;
  cas_version: number;
  suggestions?: string[];
}

/**
 * Per-conversation display settings
 */
export interface ChatSettings {
  /** Show LLM thinking blocks in assistant messages (legacy, unused by UI) */
  showThinking: boolean;
  /** Ask the model to produce a suggestions block (on by default) */
  showSuggestions: boolean;
  /** Reasoning generation on/off; off also hides thinking blocks */
  thinkingEnabled: boolean;
}

/**
 * Default chat settings for new conversations
 */
export const DEFAULT_CHAT_SETTINGS: ChatSettings = {
  showThinking: false,
  showSuggestions: true,
  thinkingEnabled: true,
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
  bridgeProvider?: string | null | undefined;
  /** Per-conversation display settings */
  settings: ChatSettings;
  /** Current server-side processing status for this conversation */
  processingStatus: "idle" | "active";
  /** Human-readable server-side processing stage label */
  processingStage: string;
  /** When server-side processing started */
  processingStartedAt?: Date | undefined;
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
