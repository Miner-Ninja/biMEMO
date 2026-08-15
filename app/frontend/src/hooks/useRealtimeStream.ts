/**
 * WebSocket-only realtime stream hook.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { authStatus, refreshAccessToken } from "@/lib/bimemo-api";

const MIN_BACKOFF = 1000;
const MAX_BACKOFF = 30000;
// The server sends a heartbeat every ~5 seconds. A socket that receives
// nothing for longer than the liveness budget is a zombie (e.g. a NAT
// gateway dropped the TCP session) — closing it triggers the normal
// reconnect path even though the WebSocket API still reports it as open.
const HEARTBEAT_CHECK_INTERVAL_MS = 5000;
const LIVENESS_TIMEOUT_MS = 20000;

export interface RealtimeEvent {
  type: string;
  data?: unknown;
  content?: string;
  request_id?: string;
}

export type ConnectionStatus = "connected" | "connecting" | "disconnected";

function websocketUrl(conversationId?: string | null): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL("/ws", `${protocol}//${window.location.host}`);
  if (conversationId) {
    url.searchParams.set("conversation_id", conversationId);
  }
  return url.toString();
}

async function ensureAuthenticated(): Promise<boolean> {
  try {
    const status = await authStatus();
    if (status.authenticated) {
      return true;
    }
    return await refreshAccessToken();
  } catch {
    return false;
  }
}

export function useRealtimeStream(
  onEvent: (event: RealtimeEvent) => void,
  conversationId?: string | null
) {
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const webSocketRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef(MIN_BACKOFF);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeRef = useRef(false);
  const onEventRef = useRef(onEvent);
  const connectRef = useRef<(() => Promise<void>) | null>(null);
  const reconnectingRef = useRef(false);
  // Bumped on every connect()/disconnect(). Each connection attempt captures
  // its generation; stale callbacks (from a superseded socket) early-return.
  const generationRef = useRef(0);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  const livenessRef = useRef<{
    timer: ReturnType<typeof setInterval> | null;
    lastMessageAt: number;
  }>({ timer: null, lastMessageAt: 0 });

  const clearLiveness = useCallback(() => {
    if (livenessRef.current.timer) {
      clearInterval(livenessRef.current.timer);
      livenessRef.current.timer = null;
    }
  }, []);

  const armLiveness = useCallback(
    (socket: WebSocket, generation: number) => {
      clearLiveness();
      livenessRef.current.lastMessageAt = Date.now();
      livenessRef.current.timer = setInterval(() => {
        if (generation !== generationRef.current) {
          clearLiveness();
          return;
        }
        if (Date.now() - livenessRef.current.lastMessageAt > LIVENESS_TIMEOUT_MS) {
          // Zombie socket: the close() fires onclose, which schedules the
          // backoff reconnect.
          clearLiveness();
          socket.close();
        }
      }, HEARTBEAT_CHECK_INTERVAL_MS);
    },
    [clearLiveness]
  );

  const cleanup = useCallback(() => {
    clearLiveness();
    if (retryRef.current) {
      clearTimeout(retryRef.current);
      retryRef.current = null;
    }
    if (webSocketRef.current) {
      webSocketRef.current.close();
      webSocketRef.current = null;
    }
  }, [clearLiveness]);

  const scheduleReconnect = useCallback(() => {
    if (!activeRef.current || reconnectingRef.current) return;
    reconnectingRef.current = true;
    void refreshAccessToken()
      .catch(() => false)
      .finally(() => {
        reconnectingRef.current = false;
        if (!activeRef.current) return;
        const delay = backoffRef.current;
        backoffRef.current = Math.min(delay * 2, MAX_BACKOFF);
        retryRef.current = setTimeout(() => {
          void connectRef.current?.();
        }, delay);
      });
  }, []);

  const dispatchEvent = useCallback((rawData: string) => {
    try {
      const data: RealtimeEvent = JSON.parse(rawData);
      if (data.type === "heartbeat") {
        return;
      }
      onEventRef.current(data);
    } catch {
      /* ignore malformed realtime messages */
    }
  }, []);

  const connectWebSocket = useCallback(async () => {
    // Capture this attempt's generation. A later connect()/disconnect() bumps
    // generationRef and invalidates every callback below.
    const generation = generationRef.current;
    const authenticated = await ensureAuthenticated();
    if (generation !== generationRef.current) return; // superseded during auth
    if (!authenticated) {
      setStatus("disconnected");
      scheduleReconnect();
      return;
    }

    const socket = new WebSocket(websocketUrl(conversationId));
    webSocketRef.current = socket;

    socket.onopen = () => {
      if (generation !== generationRef.current) return;
      setStatus("connected");
      backoffRef.current = MIN_BACKOFF;
      armLiveness(socket, generation);
    };

    socket.onmessage = (event) => {
      if (generation !== generationRef.current) return;
      livenessRef.current.lastMessageAt = Date.now();
      if (typeof event.data === "string") {
        dispatchEvent(event.data);
      }
    };

    socket.onerror = () => {
      if (generation !== generationRef.current) return;
      if (activeRef.current) {
        socket.close();
      }
    };

    socket.onclose = () => {
      if (generation !== generationRef.current) return; // stale socket — ignore
      clearLiveness();
      if (webSocketRef.current === socket) {
        webSocketRef.current = null;
      }
      setStatus("disconnected");
      if (!activeRef.current) return;
      scheduleReconnect();
    };
  }, [conversationId, dispatchEvent, scheduleReconnect, armLiveness, clearLiveness]);

  const connect = useCallback(async () => {
    generationRef.current += 1; // invalidate any in-flight/previous attempt
    activeRef.current = false;
    cleanup();
    activeRef.current = true;
    setStatus("connecting");
    await connectWebSocket();
  }, [cleanup, connectWebSocket]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  const disconnect = useCallback(() => {
    generationRef.current += 1; // invalidate the current attempt's callbacks
    activeRef.current = false;
    cleanup();
    setStatus("disconnected");
  }, [cleanup]);

  useEffect(() => {
    return () => {
      activeRef.current = false;
      cleanup();
    };
  }, [cleanup]);

  return { status, connect, disconnect };
}
