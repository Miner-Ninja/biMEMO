import { memo } from "react";

const SHIMMER_DURATION_MS = 1600;
const SHIMMER_STEP_MS = 70;

/**
 * Per-character "shimmer" text — each character fades in and out on a staggered
 * loop (the `char-shimmer` keyframe). Used for the live processing-stage label,
 * both in the message list and as the chat-input placeholder overlay.
 */
export const ShimmerText = memo(function ShimmerText({ text }: { text: string }) {
  const chars = Array.from(text);
  return (
    <span className="inline-flex whitespace-pre">
      {chars.map((ch, i) => (
        <span
          key={i}
          style={{
            animation: `char-shimmer ${SHIMMER_DURATION_MS}ms ease-in-out infinite`,
            animationDelay: `-${(i * SHIMMER_STEP_MS) % SHIMMER_DURATION_MS}ms`,
            willChange: "opacity",
          }}
        >
          {ch}
        </span>
      ))}
    </span>
  );
});
