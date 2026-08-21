/**
 * Tests for the live memory-events hook (memory-sse-stream, R2):
 * an EventSource message lands in the bounded feed and connection
 * state tracks open/error.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  withCredentials: boolean;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  closed = false;

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = Boolean(init?.withCredentials);
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent<string>);
  }
}

vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

import { useMemoryEvents } from "@/features/memory/hooks/useMemoryEvents";

describe("useMemoryEvents", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
  });

  it("connects with credentials and buffers newest-first events", async () => {
    const { result } = renderHook(() => useMemoryEvents(true));
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe("/admin/memory/events");
    expect(source.withCredentials).toBe(true);

    act(() => source.onopen?.());
    await waitFor(() => expect(result.current.connected).toBe(true));

    act(() => source.emit({ type: "graph_extractor", assertion_id: "a1" }));
    act(() => source.emit({ type: "graph_extractor", assertion_id: "a2" }));
    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.events[0].assertion_id).toBe("a2"); // newest first

    act(() => source.onerror?.());
    await waitFor(() => expect(result.current.connected).toBe(false));
  });

  it("closes the source and does not connect when disabled", () => {
    const { unmount } = renderHook(() => useMemoryEvents(true));
    const source = FakeEventSource.instances[0];
    unmount();
    expect(source.closed).toBe(true);
  });
});
