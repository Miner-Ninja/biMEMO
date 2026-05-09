const NETWORK_FETCH_ERROR_MESSAGES = [
  "failed to fetch",
  "load failed",
  "network request failed",
  "networkerror when attempting to fetch resource",
];

export function isBrowserNetworkFetchError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  if (error.name !== "TypeError") {
    return false;
  }
  const message = error.message.toLowerCase();
  return NETWORK_FETCH_ERROR_MESSAGES.some((expectedMessage) =>
    message.includes(expectedMessage),
  );
}

export function shouldKeepStreamedAssistantResponseAfterRequestError(
  error: unknown,
  streamedContent: string,
  streamedThinking: string,
): boolean {
  if (!streamedContent && !streamedThinking) {
    return false;
  }
  return isBrowserNetworkFetchError(error);
}
