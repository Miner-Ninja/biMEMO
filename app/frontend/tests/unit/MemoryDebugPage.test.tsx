/**
 * Tests for the question-tab layout of Memory Debug
 * (memory-debug-questions, R3/S3.1 and R6).
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/components/layout/ThemeProvider";
import { AuthContext, type AuthContextValue } from "@/features/auth/hooks/useAuth";

vi.mock("@/services/bimemo-api", () => ({
  apiRequest: vi.fn(),
}));

import { apiRequest } from "@/services/bimemo-api";
import { MemoryDebugPage } from "@/features/memory/MemoryDebugPage";

const mockApiRequest = vi.mocked(apiRequest);

class EventSourceMock {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  close = vi.fn();
}
vi.stubGlobal("EventSource", EventSourceMock);

window.matchMedia = vi.fn().mockImplementation((query: string) => ({
  matches: false,
  media: query,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  addListener: vi.fn(),
  removeListener: vi.fn(),
}));

const AUTH: AuthContextValue = {
  authenticated: true,
  loading: false,
  checkStatus: vi.fn(),
  refresh: vi.fn(),
  logout: vi.fn(),
  setAuthenticated: vi.fn(),
};

function mockEmptyEndpoints() {
  mockApiRequest.mockImplementation((path: string) => {
    if (path.startsWith("/admin/memory/health")) {
      return Promise.resolve({
        pipeline: { messages: 0, records: 0, assertions: 0 },
        zones: null,
        retrieval: {},
        graph: { record_queue_depth: 0, last_assertion_at: null },
        loops: {
          pruning: new Date().toISOString(),
          backup: new Date().toISOString(),
          graph_extractor: new Date().toISOString(),
        },
      });
    }
    if (path.startsWith("/admin/memory/vector-coverage")) {
      return Promise.resolve({ total: 0, embedded: 0, unembedded: 0 });
    }
    if (path.startsWith("/admin/memory/quality")) {
      return Promise.resolve({
        counts: { message: 0, record: 0, assertion: 0 },
        assertions_valid: 0,
        assertions_invalidated: 0,
        assertions_without_evidence: 0,
        assertions_with_missing_evidence: 0,
        duplicate_assertion_texts: 0,
        orphan_entities: 0,
        quality_score: 1,
      });
    }
    if (path.startsWith("/admin/memory/pipeline")) {
      return Promise.resolve({ messages: 0, records: 0, assertions: 0 });
    }
    if (path.startsWith("/admin/memory/context")) {
      return Promise.resolve({ tiers: {} });
    }
    if (path.startsWith("/admin/memory/graph/entities")) {
      return Promise.resolve({ entities: [] });
    }
    if (path.startsWith("/admin/memory/route-diagnostics/summary")) {
      return Promise.resolve({
        totals: {
          total_rows: 0,
          fallback_rows: 0,
          mismatch_rows: 0,
          long_term_rows: 0,
        },
        recent_hours: 24,
        mismatch_labels: [],
        final_intents: [],
        fallback_reasons: [],
      });
    }
    if (path.startsWith("/admin/memory/route-diagnostics")) {
      return Promise.resolve({ diagnostics: [] });
    }
    return Promise.resolve({
      entries: [],
      items: [],
      total_count: 0,
      total: 0,
      embedded: 0,
      unembedded: 0,
    });
  });
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <AuthContext.Provider value={AUTH}>
          <MemoryDebugPage />
        </AuthContext.Provider>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe("MemoryDebugPage question tabs", () => {
  beforeEach(() => {
    mockApiRequest.mockReset();
    mockEmptyEndpoints();
  });

  it("S3.1 — shows the four question tabs and keeps the status strip", async () => {
    const user = userEvent.setup();
    renderPage();

    const tabNames = [
      "What's happening",
      "What was remembered",
      "Why it answered that",
      "What's broken",
    ];
    for (const name of tabNames) {
      expect(await screen.findByRole("button", { name })).toBeTruthy();
    }
    // Switching tabs must not remove the tab bar (strip stays above it).
    for (const name of tabNames) {
      await user.click(await screen.findByRole("button", { name }));
      expect(
        await screen.findByRole("button", { name: "What's happening" })
      ).toBeTruthy();
    }
  });

  it("R6 — the broken tab reports no problems on clean data", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "What's broken" }));
    expect(await screen.findByText("No problems detected")).toBeTruthy();
  });
});
