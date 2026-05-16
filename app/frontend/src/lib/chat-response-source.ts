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

export function runtimeBootstrapHasTerminalEvent(
  payload: RuntimeBootstrapPayload | null | undefined,
): boolean {
  return Boolean(
    payload?.conversation_id &&
      payload.events?.some((event) =>
        TERMINAL_RUNTIME_EVENT_TYPES.has(String(event.event_type || "")),
      ),
  );
}

export function chatInputIsDisabled({
  backendRunActive,
  localRequestActive,
  uploadActive,
}: ChatInputAvailabilityState): boolean {
  return backendRunActive || localRequestActive || uploadActive;
}

export function markStreamTerminalError(
  stream: ActiveAssistantStreamState,
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
