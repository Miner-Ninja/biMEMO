/**
 * Decide whether the chat UI should play the local pseudo-stream animation.
 *
 * The backend signals real token streaming via the `stream_enabled` flag on
 * the chat HTTP response (driven by `BIMEMO_LLM_RESPONSE_STREAM`). When that
 * flag is true the helper short-circuits to false so the local animation
 * never fires — even if the streaming path emitted no visible deltas (for
 * example, when the model returned the `answer` field empty). When the flag
 * is false or absent, the legacy implicit detection takes over: pseudo-stream
 * fires only if structured content is present, has visible material, and
 * neither streaming buffer accumulated anything.
 */

export type StructuredAssistantContentLike =
  | {
      answer?: string;
      thinking?: string | null;
      sql?: string | null;
    }
  | null
  | undefined;

export function shouldUseStructuredPseudoStream(
  structuredContent: StructuredAssistantContentLike,
  streamedContent: string,
  streamedThinking: string,
  streamEnabled: boolean | undefined
): boolean {
  if (streamEnabled === true) {
    return false;
  }
  if (!structuredContent) {
    return false;
  }
  if (streamedContent || streamedThinking) {
    return false;
  }
  return Boolean(
    structuredContent.answer || structuredContent.thinking || structuredContent.sql
  );
}
