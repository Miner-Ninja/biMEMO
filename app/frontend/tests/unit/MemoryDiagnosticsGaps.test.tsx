/**
 * Tests for the memory diagnostics-gaps panels (stage 3, PR-A).
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

vi.mock("@/services/bimemo-api", () => ({
  apiRequest: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

import { RecordQueuePanel } from "@/features/memory/components/RecordQueuePanel";
import { apiRequest } from "@/services/bimemo-api";

const apiRequestMock = vi.mocked(apiRequest);

describe("memory diagnostics gaps", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
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
