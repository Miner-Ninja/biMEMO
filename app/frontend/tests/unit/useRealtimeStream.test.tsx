import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRealtimeStream } from "@/hooks/useRealtimeStream";

vi.mock("@/lib/bimemo-api", () => ({
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
});
