import { useEffect, useRef, useState } from "react";

/**
 * Live memory-pipeline events over Server-Sent Events.
 *
 * The browser's EventSource carries the session cookie same-origin, so
 * the stream authenticates like any other admin request. It also
 * reconnects on its own; we only mirror connection state and keep a
 * bounded buffer of the most recent events for display.
 */
export interface MemoryEvent {
  type: string;
  timestamp?: number;
  [key: string]: unknown;
}

const FEED_LIMIT = 20;

export function useMemoryEvents(enabled: boolean): {
  events: MemoryEvent[];
  connected: boolean;
} {
  const [events, setEvents] = useState<MemoryEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!enabled) return undefined;
    const source = new EventSource("/admin/memory/events", {
      withCredentials: true,
    });
    sourceRef.current = source;

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (message: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(message.data) as MemoryEvent;
        setEvents((previous) => [parsed, ...previous].slice(0, FEED_LIMIT));
      } catch {
        // Ignore malformed frames; heartbeats are comments, not messages.
      }
    };

    return () => {
      source.close();
      sourceRef.current = null;
      setConnected(false);
    };
  }, [enabled]);

  return { events, connected };
}
