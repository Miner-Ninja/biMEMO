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
  value: unknown
): Record<string, unknown> {
  return {
    ...body,
    run_id: pendingInput.run_id,
    pending_input_id: pendingInput.pending_input_id,
    pending_input_version: pendingInput.cas_version,
    pending_input_value: value,
  };
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
