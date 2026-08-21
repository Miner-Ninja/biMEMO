/**
 * Tests for the user-facing Memory page (memory-page contract).
 *
 * S5.1 — confirmation dialog, then password re-auth, then the request
 * fires with the one-time token. S5.2 — cancelling either step sends
 * nothing. S3.1/S3.2 — history toggle hides/shows invalidated facts.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "@/components/layout/ThemeProvider";
import { AuthContext, type AuthContextValue } from "@/hooks/useAuth";

// Node's experimental bare `localStorage` global shadows jsdom's — give
// the theme/settings chain one real in-memory implementation.
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

window.matchMedia = vi.fn().mockImplementation((query: string) => ({
  matches: false,
  media: query,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  addListener: vi.fn(),
  removeListener: vi.fn(),
}));

vi.mock("@/api/memory", () => ({
  listMemoryEntities: vi.fn(),
  searchMemoryEntities: vi.fn(),
  getMemoryEntity: vi.fn(),
  resolveMemoryRecord: vi.fn(),
  invalidateMemoryAssertion: vi.fn(),
}));

vi.mock("@/lib/bimemo-api", () => {
  class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }
  type NormalizedError = {
    kind: "http" | "contract" | "network";
    status: number | null;
    message: string;
  };
  const normalizeApiError = (err: unknown): NormalizedError => {
    if (err instanceof ApiError) {
      return {
        kind: "http" as const,
        status: err.status,
        message: err.message,
      };
    }
    if (err instanceof Error) {
      return {
        kind: "network" as const,
        status: null,
        message: err.message,
      };
    }
    return { kind: "network" as const, status: null, message: String(err) };
  };
  const errorToDisplayText = (err: unknown, t: (key: string) => string) => {
    const n = normalizeApiError(err);
    if (n.kind === "contract") {
      return t("errors.contract");
    }
    if (n.kind === "network") {
      return t("errors.network");
    }
    return n.message || t("errors.http");
  };
  return { apiRequest: vi.fn(), ApiError, normalizeApiError, errorToDisplayText };
});

import { MemoryPage } from "@/pages/admin/MemoryPage";
import {
  getMemoryEntity,
  invalidateMemoryAssertion,
  listMemoryEntities,
} from "@/api/memory";
import { apiRequest } from "@/lib/bimemo-api";

const listMock = vi.mocked(listMemoryEntities);
const getEntityMock = vi.mocked(getMemoryEntity);
const invalidateMock = vi.mocked(invalidateMemoryAssertion);
const apiRequestMock = vi.mocked(apiRequest);

let queryClient: QueryClient;

const ENTITY = {
  id: "e1",
  entity_type: "person",
  canonical_name: "alice",
  aliases: [],
  created_at: "2026-08-01T10:00:00",
  active_assertions: 1,
};

function makeAssertion(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "a1",
    subject_entity_id: "e1",
    relation: "prefers",
    object_entity_id: null,
    object_value: "docker-compose",
    assertion_text: "Alice prefers docker-compose",
    trust_level: "medium",
    assertion_scope: "long_term",
    valid_from: "2026-08-01T10:00:00",
    invalid_from: null,
    invalidated_by_assertion_id: null,
    source_record_ids: [],
    created_at: "2026-08-01T10:00:00",
    ...overrides,
  };
}

const AUTH: AuthContextValue = {
  authenticated: true,
  loading: false,
  checkStatus: vi.fn(),
  refresh: vi.fn(),
  logout: vi.fn(),
  setAuthenticated: vi.fn(),
};

function renderPage() {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ThemeProvider>
          <AuthContext.Provider value={AUTH}>
            <MemoryPage />
          </AuthContext.Provider>
        </ThemeProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

async function openEntityFacts(user: ReturnType<typeof userEvent.setup>) {
  const trigger = await screen.findByText("Alice");
  await user.click(trigger);
  await screen.findByText("Alice prefers docker-compose");
}

describe("MemoryPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    listMock.mockResolvedValue({ entities: [ENTITY], total: 1 });
    getEntityMock.mockResolvedValue({
      entity: ENTITY,
      assertions: [makeAssertion()],
    });
  });

  it("S5.1 — confirm + re-auth precede the invalidation request", async () => {
    const user = userEvent.setup();
    apiRequestMock.mockResolvedValue({ reauth_token: "tok-1" });
    invalidateMock.mockResolvedValue({ invalidated: true });
    renderPage();
    await openEntityFacts(user);

    await user.click(screen.getByRole("button", { name: "Forget" }));
    expect(invalidateMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(invalidateMock).not.toHaveBeenCalled();

    const password = await screen.findByPlaceholderText("Enter your password");
    await user.type(password, "secret");
    // The re-auth dialog reuses the "Confirm" label on its submit button.
    const confirmButtons = screen.getAllByRole("button", { name: "Confirm" });
    await user.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() =>
      expect(invalidateMock).toHaveBeenCalledWith("a1", "forget", "tok-1")
    );
  });

  it("S5.2 — cancelling the confirmation sends nothing", async () => {
    const user = userEvent.setup();
    renderPage();
    await openEntityFacts(user);

    await user.click(screen.getByRole("button", { name: "Incorrect" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(apiRequestMock).not.toHaveBeenCalled();
    expect(invalidateMock).not.toHaveBeenCalled();
  });

  it("S5.2 — closing the password prompt sends nothing", async () => {
    const user = userEvent.setup();
    renderPage();
    await openEntityFacts(user);

    await user.click(screen.getByRole("button", { name: "Forget" }));
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await screen.findByPlaceholderText("Enter your password");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(invalidateMock).not.toHaveBeenCalled();
  });

  it("S3.1/S3.2 — history toggle reveals invalidated facts", async () => {
    const user = userEvent.setup();
    getEntityMock.mockResolvedValue({
      entity: ENTITY,
      assertions: [
        makeAssertion(),
        makeAssertion({
          id: "a2",
          assertion_text: "Alice prefers terraform",
          invalid_from: "2026-08-05T10:00:00",
        }),
      ],
    });
    renderPage();
    await openEntityFacts(user);

    expect(screen.queryByText("Alice prefers terraform")).toBeNull();
    await user.click(screen.getByRole("switch"));
    await screen.findByText("Alice prefers terraform");
  });
});
