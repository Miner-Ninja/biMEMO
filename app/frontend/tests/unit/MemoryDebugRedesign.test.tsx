/**
 * Tests for the memory-debug redesign (memory-debug-redesign contract).
 *
 * S1.2 — single-row tabs switch sections. S2.1 — the first context
 * tier starts expanded. S4.2 — the shared assertion card mutes
 * invalidated assertions and shows the invalidation date.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@/components/layout/ThemeProvider";
import { AuthContext, type AuthContextValue } from "@/hooks/useAuth";

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

class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
vi.stubGlobal("ResizeObserver", ResizeObserverMock);

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

vi.mock("@/lib/bimemo-api", () => ({
  apiRequest: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

import { MemoryDebugPage } from "@/pages/admin/MemoryDebugPage";
import { AssertionCard } from "@/components/admin/memory-debug/AssertionCard";
import { ContextSnapshotPanel } from "@/components/admin/memory-debug/ContextSnapshotPanel";
import { apiRequest } from "@/lib/bimemo-api";

const apiRequestMock = vi.mocked(apiRequest);

const AUTH: AuthContextValue = {
  authenticated: true,
  loading: false,
  checkStatus: vi.fn(),
  refresh: vi.fn(),
  logout: vi.fn(),
  setAuthenticated: vi.fn(),
};

function withProviders(node: React.ReactNode) {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <AuthContext.Provider value={AUTH}>{node}</AuthContext.Provider>
      </ThemeProvider>
    </MemoryRouter>
  );
}

function mockEndpoints() {
  apiRequestMock.mockImplementation((url: string) => {
    if (url.startsWith("/admin/memory/pipeline")) {
      return Promise.resolve({ messages: 1, records: 1, assertions: 1 });
    }
    if (url.startsWith("/admin/memory/quality")) {
      return Promise.resolve({
        counts: { message: 1, record: 1, assertion: 1 },
        assertions_valid: 1,
        assertions_invalidated: 0,
        assertions_without_evidence: 0,
        assertions_with_missing_evidence: 0,
        duplicate_assertion_texts: 0,
        orphan_entities: 0,
        quality_score: 0.9,
      });
    }
    if (url.startsWith("/admin/memory/route-diagnostics/summary")) {
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
    if (url.startsWith("/admin/memory/route-diagnostics")) {
      return Promise.resolve({ diagnostics: [] });
    }
    if (url.startsWith("/admin/memory/context")) {
      return Promise.resolve({
        tiers: {
          recent_messages: {
            count: 1,
            entries: [{ role: "user", content: "first-tier-entry", created_at: "t1" }],
          },
          records: {
            count: 1,
            entries: [{ content: "second-tier-entry", created_at: "t2" }],
          },
        },
      });
    }
    return Promise.resolve({ entries: [], assertions: [], total_count: 0 });
  });
}

describe("memory-debug redesign", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
    mockEndpoints();
  });

  it("S1.2 — question tabs switch the rendered content", async () => {
    const user = userEvent.setup();
    withProviders(<MemoryDebugPage />);
    // "What's happening" is default; its quality card appears.
    await screen.findByText("Quality score");
    // The context snapshot now lives under "Why it answered that".
    await user.click(
      await screen.findByRole("button", { name: "Why it answered that" })
    );
    await user.click(await screen.findByRole("button", { name: "Context snapshot" }));
    await screen.findByText("first-tier-entry");
  });

  it("S2.1 — the first context tier is expanded without a click", async () => {
    withProviders(<ContextSnapshotPanel refreshToken={0} />);
    await screen.findByText("first-tier-entry");
    expect(screen.queryByText("second-tier-entry")).toBeNull();
  });

  it("S4.2 — invalidated assertions are muted with their date", () => {
    const { container } = withProviders(
      <AssertionCard
        data={{
          id: "a1",
          text: "Stale fact",
          trustLevel: "medium",
          invalidFrom: "2026-08-05T10:00:00",
        }}
      />
    );
    expect(screen.getByText(/invalidated 2026-08-05/)).toBeInTheDocument();
    expect(container.querySelector(".opacity-60")).not.toBeNull();
  });

  it("S4.2 — active assertions show the trust label, no invalidation", () => {
    withProviders(
      <AssertionCard
        data={{
          id: "a2",
          text: "Fresh fact",
          trustLevel: "high",
          validFrom: "2026-08-01T10:00:00",
        }}
      />
    );
    expect(screen.getByText("High trust")).toBeInTheDocument();
    expect(screen.queryByText(/invalidated/)).toBeNull();
  });
});
