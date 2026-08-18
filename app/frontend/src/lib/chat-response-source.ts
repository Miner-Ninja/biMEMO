import { generateId as generateUuid } from "@/lib/utils";

export const DEFAULT_STREAM_ERROR_MESSAGE =
  "Internal error while processing the request";

export type ActiveAssistantStreamState = {
  assistantMessageId: string | null;
  conversationId: string | null;
  requestId: string | null;
  runId: string | null;
  voiceUserMessageId: string | null;
  httpCompleted: boolean;
  finalReceived: boolean;
  realtimeCompleted: boolean;
};

export type RuntimeJournalEvent = {
  run_id?: string;
  event_type?: string;
  payload?: Record<string, unknown>;
};

export type RuntimeBootstrapPayload = {
  conversation_id?: string;
  events?: RuntimeJournalEvent[];
};

export type ChatInputAvailabilityState = {
  backendRunActive: boolean;
  localRequestActive: boolean;
  uploadActive: boolean;
};

export type PendingInputBinding = {
  run_id: string;
  pending_input_id: string;
  cas_version: number;
};

export function bindPendingInputReply(
  body: Record<string, unknown>,
  pendingInput: PendingInputBinding,
  value: unknown,
  clientMessageId: string
): Record<string, unknown> {
  return {
    ...body,
    run_id: pendingInput.run_id,
    pending_input_id: pendingInput.pending_input_id,
    pending_input_version: pendingInput.cas_version,
    pending_input_value: value,
    client_message_id: clientMessageId,
  };
}

/**
 * Client-generated identifiers for one POST /chat send.
 *
 * A regular message uses ONE pre-generated UUID as both `run_id` (the turn
 * identity) and `client_message_id` (the stored message row identity).
 * A retry after a network failure resends the same pair: the server
 * dedups by (conversation, client_message_id) and replays the turn by
 * run_id, so the retry never creates a duplicate row.
 * A pending-input answer keeps the turn's run_id but takes a fresh
 * client_message_id — each answer is its own message row.
 */
export type ChatSendIds = {
  runId: string;
  clientMessageId: string;
};

export type ChatSendIdSource = "new" | "retry" | "pending-input";

export type ResolvedChatSendIds = ChatSendIds & { source: ChatSendIdSource };

/**
 * A send that failed on the wire. Resending the same text in the same
 * conversation retries the stored id pair instead of generating new ones.
 */
export type FailedChatSend = {
  conversationId: string;
  content: string;
  runId: string;
  clientMessageId: string;
  userMessageId: string;
  assistantMessageId: string;
};

/** Fresh identifiers for a regular message: one UUID serves both fields. */
export function newChatSendIds(generateId: () => string = generateUuid): ChatSendIds {
  const runId = generateId();
  return { runId, clientMessageId: runId };
}

/** Bind a regular (non pending-input) send to its identifiers. */
export function bindSendIds(
  body: Record<string, unknown>,
  ids: ChatSendIds
): Record<string, unknown> {
  return {
    ...body,
    run_id: ids.runId,
    client_message_id: ids.clientMessageId,
  };
}

/**
 * Decide the identifiers for the next send:
 * - pending-input answer: the turn's run_id + a fresh client_message_id;
 * - same text in the same conversation as a failed send: that send's ids
 *   (retry after failure — the server dedups, no duplicate row);
 * - otherwise: a fresh id pair (reworded or brand-new message).
 */
export function resolveChatSendIds(
  previousFailure: FailedChatSend | null,
  current: { conversationId: string; content: string },
  pendingInput: { run_id: string } | null,
  generateId: () => string = generateUuid
): ResolvedChatSendIds {
  if (pendingInput?.run_id) {
    return {
      runId: pendingInput.run_id,
      clientMessageId: generateId(),
      source: "pending-input",
    };
  }
  if (
    previousFailure !== null &&
    previousFailure.conversationId === current.conversationId &&
    previousFailure.content === current.content
  ) {
    return {
      runId: previousFailure.runId,
      clientMessageId: previousFailure.clientMessageId,
      source: "retry",
    };
  }
  const runId = generateId();
  return { runId, clientMessageId: runId, source: "new" };
}

export const TERMINAL_RUNTIME_EVENT_TYPES = new Set([
  "final_accepted",
  "run_completed",
  "run_failed",
  "run_stopped",
  "run_cancelled",
]);

export function shouldApplyHttpChatResponse(streamFinalReceived: boolean): boolean {
  return !streamFinalReceived;
}

export function shouldFinalizeAssistantStream(
  stream: ActiveAssistantStreamState
): boolean {
  return Boolean(
    stream.httpCompleted && stream.realtimeCompleted && stream.finalReceived
  );
}

export function runtimeBootstrapHasTerminalEvent(
  payload: RuntimeBootstrapPayload | null | undefined
): boolean {
  return Boolean(
    payload?.conversation_id &&
    payload.events?.some((event) =>
      TERMINAL_RUNTIME_EVENT_TYPES.has(String(event.event_type || ""))
    )
  );
}

export function runtimeBootstrapPausedEvent(
  payload: RuntimeBootstrapPayload | null | undefined
): RuntimeJournalEvent | null {
  const events = payload?.events ?? [];
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.event_type === "run_paused") {
      return event;
    }
    if (TERMINAL_RUNTIME_EVENT_TYPES.has(String(event.event_type || ""))) {
      return null;
    }
  }
  return null;
}

export function chatInputIsDisabled({
  backendRunActive,
  localRequestActive,
  uploadActive,
}: ChatInputAvailabilityState): boolean {
  return backendRunActive || localRequestActive || uploadActive;
}

export function markStreamTerminalError(
  stream: ActiveAssistantStreamState
): ActiveAssistantStreamState {
  return {
    ...stream,
    finalReceived: true,
    httpCompleted: true,
  };
}

export function resolveStreamErrorMessage(message?: string | null): string {
  const trimmed = message?.trim();
  return trimmed || DEFAULT_STREAM_ERROR_MESSAGE;
}

export type PausedRunBinding = {
  conversationId: string;
  runId: string;
};

/**
 * A paused turn (waiting on a tool confirmation) resumes through a separate
 * /confirm request, and the resumed turn publishes its lifecycle events under
 * that confirm request's request_id — one the page that started the original
 * turn never saw. The only stable link between the original turn and its
 * resume is the turn's run_id.
 *
 * Classifies such events against a paused-run binding:
 * - "activity": a confirmation lifecycle event (carries no run_id) — the
 *   user's decision just landed and the resume is about to run / is running.
 * - "resync": a turn outcome (final/paused/failed/cancelled) whose run_id
 *   matches the binding — the resume reached that outcome and the server
 *   state is complete; the page should re-sync from it.
 * - null: an event unrelated to the paused run — handled by the normal
 *   active-stream path (or ignored).
 */
export function classifyPausedResumeEvent(
  event: {
    type: string;
    data?: unknown;
  },
  pausedRun: PausedRunBinding | null,
  options: {
    activeAssistantMessageId: string | null;
    conversationId: string | null;
  }
): "activity" | "resync" | null {
  if (
    pausedRun === null ||
    !options.conversationId ||
    pausedRun.conversationId !== options.conversationId ||
    options.activeAssistantMessageId !== null
  ) {
    return null;
  }
  if (event.type === "confirmation") {
    return "activity";
  }
  if (
    event.type === "final" ||
    event.type === "paused" ||
    event.type === "failed" ||
    event.type === "cancelled"
  ) {
    const data =
      event.data && typeof event.data === "object"
        ? (event.data as Record<string, unknown>)
        : null;
    const eventRunId = typeof data?.run_id === "string" ? data.run_id : "";
    return eventRunId === pausedRun.runId ? "resync" : null;
  }
  return null;
}
