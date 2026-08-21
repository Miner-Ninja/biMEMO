import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRealtimeStream } from "@/hooks/useRealtimeStream";

vi.mock("@/services/bimemo-api", () => ({
  authStatus: vi.fn(async () => ({ authenticated: true })),
  refreshAccessToken: vi.fn(async () => true),
}));

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  url: string;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  close() {
    this.onclose?.();
  }
}

describe("useRealtimeStream", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("dispatches WebSocket messages with the shared event contract", async () => {
    const onEvent = vi.fn();
    const { result } = renderHook(() => useRealtimeStream(onEvent, "conv-1"));

    await act(async () => {
      await result.current.connect();
    });

    const socket = FakeWebSocket.instances[0];
    expect(socket.url).toContain("/ws?conversation_id=conv-1");

    act(() => {
      socket.onopen?.();
      socket.onmessage?.(
        new MessageEvent("message", {
          data: JSON.stringify({ type: "final", request_id: "req-1" }),
        })
      );
    });

    expect(onEvent).toHaveBeenCalledWith({ type: "final", request_id: "req-1" });
  });

  it("reconnects with WebSocket when the connection closes", async () => {
    vi.useFakeTimers();
    const onEvent = vi.fn();
    const { result } = renderHook(() => useRealtimeStream(onEvent, "conv-2"));

    await act(async () => {
      await result.current.connect();
    });

    const socket = FakeWebSocket.instances[0];
    act(() => {
      socket.onclose?.();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(FakeWebSocket.instances[1].url).toContain("/ws?conversation_id=conv-2");
  });

  it("closes a zombie socket that stops sending heartbeats", async () => {
    vi.useFakeTimers();
    const onEvent = vi.fn();
    const { result } = renderHook(() => useRealtimeStream(onEvent, "conv-3"));

    await act(async () => {
      await result.current.connect();
    });

    const socket = FakeWebSocket.instances[0];
    const closeSpy = vi.spyOn(socket, "close");
    act(() => {
      socket.onopen?.();
    });

    // The watchdog fires once the liveness budget elapses without any
    // received frame (heartbeats included).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(25000);
    });

    expect(closeSpy).toHaveBeenCalledTimes(1);
  });

  it("keeps the connection alive while heartbeats arrive", async () => {
    vi.useFakeTimers();
    const onEvent = vi.fn();
    const { result } = renderHook(() => useRealtimeStream(onEvent, "conv-4"));

    await act(async () => {
      await result.current.connect();
    });

    const socket = FakeWebSocket.instances[0];
    const closeSpy = vi.spyOn(socket, "close");
    act(() => {
      socket.onopen?.();
    });

    // A heartbeat every 5s: the pipe is alive, no forced reconnect.
    for (let i = 0; i < 5; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
      act(() => {
        socket.onmessage?.(
          new MessageEvent("message", {
            data: JSON.stringify({ type: "heartbeat" }),
          })
        );
      });
    }

    expect(closeSpy).not.toHaveBeenCalled();
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("resets the liveness window on any received message", async () => {
    vi.useFakeTimers();
    const onEvent = vi.fn();
    const { result } = renderHook(() => useRealtimeStream(onEvent, "conv-5"));

    await act(async () => {
      await result.current.connect();
    });

    const socket = FakeWebSocket.instances[0];
    const closeSpy = vi.spyOn(socket, "close");
    act(() => {
      socket.onopen?.();
    });

    // A real message at t=10s resets the window: the watchdog must not
    // fire at open+20s, only after 20s of silence from t=10s.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    act(() => {
      socket.onmessage?.(
        new MessageEvent("message", {
          data: JSON.stringify({ type: "final", request_id: "req-1" }),
        })
      );
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(closeSpy).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(closeSpy).toHaveBeenCalledTimes(1);
  });
});
