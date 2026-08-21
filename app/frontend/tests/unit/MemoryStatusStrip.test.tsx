/**
 * Tests for the memory status strip (memory-debug-questions, R1/R2):
 * five indicators derived from health/coverage/trace endpoints.
 */

import { render, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/bimemo-api", () => ({
  apiRequest: vi.fn(),
}));

import { apiRequest } from "@/services/bimemo-api";
import { MemoryStatusStrip } from "@/features/memory/components/MemoryStatusStrip";

const mockApiRequest = vi.mocked(apiRequest);

const HOUR_MS = 3600 * 1000;

function mockEndpoints({
  loops,
  coverage,
  traceTimestamp,
}: {
  loops: Record<string, string | null>;
  coverage: { total: number; embedded: number; unembedded: number };
  traceTimestamp: string | null;
}) {
  mockApiRequest.mockImplementation((path: string) => {
    if (path.startsWith("/admin/memory/health")) {
      return Promise.resolve({
        pipeline: { messages: 0, records: 0, assertions: 0 },
        zones: null,
        retrieval: {},
        graph: { record_queue_depth: 0, last_assertion_at: null },
        loops,
      });
    }
    if (path.startsWith("/admin/memory/vector-coverage")) {
      return Promise.resolve(coverage);
    }
    return Promise.resolve({
      entries: traceTimestamp
        ? [{ timestamp: traceTimestamp, decision: "stored" }]
        : [],
    });
  });
}

async function renderedIndicators(container: HTMLElement) {
  await waitFor(() => {
    expect(container.querySelectorAll("[class*='rounded-full']").length).toBe(5);
  });
  return container.querySelectorAll("[class*='rounded-full']");
}

describe("MemoryStatusStrip", () => {
  beforeEach(() => {
    mockApiRequest.mockReset();
  });

  it("S1.1 — all green when loops fresh and vectors fully embedded", async () => {
    const fresh = new Date(Date.now() - HOUR_MS).toISOString();
    mockEndpoints({
      loops: { graph_extractor: fresh, pruning: fresh, backup: fresh },
      coverage: { total: 10, embedded: 10, unembedded: 0 },
      traceTimestamp: new Date().toISOString(),
    });

    const { container } = render(<MemoryStatusStrip refreshToken={0} />);
    const indicators = await renderedIndicators(container);
    indicators.forEach((indicator) => {
      expect(indicator.className).toContain("bg-green-500");
    });
  });

  it("S1.2 — pruning indicator red when its heartbeat is missing", async () => {
    const fresh = new Date(Date.now() - HOUR_MS).toISOString();
    mockEndpoints({
      loops: { graph_extractor: fresh, backup: fresh },
      coverage: { total: 10, embedded: 10, unembedded: 0 },
      traceTimestamp: new Date().toISOString(),
    });

    const { container } = render(<MemoryStatusStrip refreshToken={0} />);
    const indicators = await renderedIndicators(container);
    expect(indicators[3].className).toContain("bg-red-500");
  });

  it("S1.3 — vectors indicator neutral when coverage total is zero", async () => {
    const fresh = new Date(Date.now() - HOUR_MS).toISOString();
    mockEndpoints({
      loops: { graph_extractor: fresh, pruning: fresh, backup: fresh },
      coverage: { total: 0, embedded: 0, unembedded: 0 },
      traceTimestamp: fresh,
    });

    const { container } = render(<MemoryStatusStrip refreshToken={0} />);
    const indicators = await renderedIndicators(container);
    expect(indicators[2].className).toContain("bg-gray-400");
  });
});
