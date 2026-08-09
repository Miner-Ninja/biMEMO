/**
 * Tests for the memory diagnostics-gaps panels (stage 3, PR-A):
 * extractor trace, record queue, and vector coverage.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => (key in store ? store[key] : null),
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();
Object.defineProperty(window, "localStorage", { value: localStorageMock });
Object.defineProperty(globalThis, "localStorage", { value: localStorageMock });

vi.mock("@/lib/bimemo-api", () => ({
  apiRequest: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

import { ExtractorTracePanel } from "@/components/admin/memory-debug/ExtractorTracePanel";
import { RecordQueuePanel } from "@/components/admin/memory-debug/RecordQueuePanel";
import { apiRequest } from "@/lib/bimemo-api";

const apiRequestMock = vi.mocked(apiRequest);

describe("memory diagnostics gaps", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("extractor trace renders a decision with its detail", async () => {
    apiRequestMock.mockResolvedValue({
      entries: [
        {
          timestamp: "2026-08-09T10:00:00.000",
          decision: "assertion_stored",
          assertion_id: "a1",
          relation: "prefers",
        },
      ],
    });
    render(<ExtractorTracePanel refreshToken={0} />);
    // The decision-specific detail is shown (first entry opens by default).
    await waitFor(() => expect(screen.getByText(/prefers/)).toBeInTheDocument());
    expect(screen.getByText(/a1/)).toBeInTheDocument();
  });

  it("record queue shows a pending item and never calls a pruning op", async () => {
    apiRequestMock.mockResolvedValue({
      items: [
        {
          queue_id: "q1",
          conversation_id: "c1",
          session_id: "s1",
          source_message_ids: ["m1", "m2"],
          injection_flagged: true,
          attempts: 3,
          created_at: "2026-08-09T10:00:00",
        },
      ],
    });
    render(<RecordQueuePanel refreshToken={0} />);
    await waitFor(() =>
      expect(screen.getByText("injection flagged")).toBeInTheDocument()
    );
    // Read-only view hits only the peek route.
    const urls = apiRequestMock.mock.calls.map((call) => String(call[0]));
    expect(urls.every((u) => u.startsWith("/admin/memory/record-queue"))).toBe(true);
  });
});
