/**
 * Tests for the entity detail page (memory-entity-page, stage 3 PR-B):
 * the fetched entity renders its assertion and a clickable neighbor
 * link resolved from the `related` map.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { render, screen, waitFor } from "@testing-library/react";
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
  ApiError: class ApiError extends Error {},
}));

import { EntityDetailPage } from "@/pages/admin/EntityDetailPage";
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

describe("EntityDetailPage", () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it("renders the entity, its assertion, and a resolved neighbor link", async () => {
    apiRequestMock.mockResolvedValue({
      entity: {
        id: "e1",
        entity_type: "person",
        canonical_name: "alice",
        aliases: ["ally"],
        created_at: "2026-08-01T10:00:00",
      },
      assertions: [
        {
          id: "a1",
          subject_entity_id: "e1",
          relation: "uses",
          object_entity_id: "e2",
          object_value: null,
          assertion_text: "Alice uses Docker",
          trust_level: "high",
          valid_from: "2026-08-01T10:00:00",
          invalid_from: null,
          created_at: "2026-08-01T10:00:00",
          direction: "subject",
        },
      ],
      related: {
        e2: { id: "e2", canonical_name: "docker", entity_type: "tool" },
      },
    });

    render(
      <AuthContext.Provider value={AUTH}>
        <MemoryRouter initialEntries={["/admin/memory-debug/entity/e1"]}>
          <Routes>
            <Route
              path="/admin/memory-debug/entity/:entityId"
              element={<EntityDetailPage />}
            />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    );

    await waitFor(() =>
      expect(screen.getByText("Alice uses Docker")).toBeInTheDocument()
    );
    // The neighbor entity is resolved to a clickable traversal link.
    const link = screen.getByRole("link", { name: /Docker/ });
    expect(link).toHaveAttribute("href", "/admin/memory-debug/entity/e2");
  });

  it("shows a not-found state on 404", async () => {
    apiRequestMock.mockRejectedValue(new Error("404 Not Found"));
    render(
      <AuthContext.Provider value={AUTH}>
        <MemoryRouter initialEntries={["/admin/memory-debug/entity/missing"]}>
          <Routes>
            <Route
              path="/admin/memory-debug/entity/:entityId"
              element={<EntityDetailPage />}
            />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    );
    await waitFor(() =>
      expect(screen.getByText("Entity not found.")).toBeInTheDocument()
    );
  });
});
