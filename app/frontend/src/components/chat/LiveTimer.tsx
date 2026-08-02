import { useEffect, useRef, useState } from "react";

interface LiveTimerProps {
  /** performance.now() timestamp when the timer started */
  startedAt: number;
  /** If set, display this frozen value instead of ticking */
  frozenMs?: number;
}

function formatTime(ms: number): string {
  if (ms < 60_000) {
    return (ms / 1000).toFixed(2) + "s";
  }
  const minutes = Math.floor(ms / 60_000);
  const remainderSec = (ms % 60_000) / 1000;
  return `${minutes}m ${remainderSec.toFixed(2)}s`;
}

/**
 * Live timer that updates at ~60fps using requestAnimationFrame.
 * Uses tabular-nums for jitter-free digit rendering.
 */
export function LiveTimer({ startedAt, frozenMs }: LiveTimerProps) {
  const [display, setDisplay] = useState(() =>
    frozenMs != null ? formatTime(frozenMs) : formatTime(performance.now() - startedAt)
  );
  const rafRef = useRef<number>(0);

  useEffect(() => {
    if (frozenMs != null) {
      setDisplay(formatTime(frozenMs));
      return;
    }

    const tick = () => {
      setDisplay(formatTime(performance.now() - startedAt));
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [startedAt, frozenMs]);

  return (
    <span className="tabular-nums text-xs text-muted-foreground min-w-[4ch] text-right">
      {display}
    </span>
  );
}
