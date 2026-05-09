export const DEFAULT_STREAM_ERROR_MESSAGE =
  "Internal error while processing the request";

export type ActiveAssistantStreamState = {
  assistantMessageId: string | null;
  conversationId: string | null;
  voiceUserMessageId: string | null;
  httpCompleted: boolean;
  finalReceived: boolean;
  sseCompleted: boolean;
};

export function shouldApplyHttpChatResponse(streamFinalReceived: boolean): boolean {
  return !streamFinalReceived;
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
