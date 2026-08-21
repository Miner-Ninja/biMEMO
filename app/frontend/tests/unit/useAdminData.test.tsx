/**
 * Tests for useAdminData: the cached admin server-state hook.
 * Covers the public shape (data, loading, error, setError, refetch),
 * single-flight reads per endpoint (two consumers share one request),
 * and the display precedence of a manual error over the query's own
 * failure.
 */
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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

import { ApiError, apiRequest } from "@/lib/bimemo-api";
import { useAdminData } from "@/hooks/useAdminData";

const apiRequestMock = vi.mocked(apiRequest);

let queryClient: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useAdminData", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  });

  it("resolves the endpoint through apiRequest and exposes the settled shape", async () => {
    apiRequestMock.mockResolvedValue({ enabled: true });
    const { result } = renderHook(
      () => useAdminData<{ enabled: boolean }>("/admin/integrations"),
      { wrapper }
    );
    expect(result.current.loading).toBe(true);
    expect(result.current.data).toBeNull();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ enabled: true });
    expect(result.current.error).toBe("");
    expect(apiRequestMock).toHaveBeenCalledTimes(1);
    expect(apiRequestMock).toHaveBeenCalledWith("/admin/integrations");
  });

  it("makes one request per endpoint when two consumers share the cache", async () => {
    apiRequestMock.mockResolvedValue({ enabled: true });
    const first = renderHook(
      () => useAdminData<{ enabled: boolean }>("/admin/integrations"),
      { wrapper }
    );
    const second = renderHook(
      () => useAdminData<{ enabled: boolean }>("/admin/integrations"),
      { wrapper }
    );
    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
    });
    expect(apiRequestMock).toHaveBeenCalledTimes(1);
    expect(first.result.current.data).toEqual(second.result.current.data);
  });

  it("displays the server's message when the query fails", async () => {
    apiRequestMock.mockRejectedValue(new ApiError(500, "boom"));
    const { result } = renderHook(
      () => useAdminData<{ enabled: boolean }>("/admin/integrations"),
      { wrapper }
    );
    await waitFor(() => expect(result.current.error).not.toBe(""));
    expect(result.current.error).toBe("boom");
    expect(result.current.data).toBeNull();
  });

  it("lets a manual error take precedence over the query state", async () => {
    apiRequestMock.mockResolvedValue({ enabled: true });
    const { result } = renderHook(
      () => useAdminData<{ enabled: boolean }>("/admin/integrations"),
      { wrapper }
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      result.current.setError("Manual failure");
    });
    expect(result.current.error).toBe("Manual failure");
  });

  it("exposes a refetch that re-reads the endpoint", async () => {
    apiRequestMock
      .mockResolvedValueOnce({ enabled: false })
      .mockResolvedValueOnce({ enabled: true });
    const { result } = renderHook(
      () => useAdminData<{ enabled: boolean }>("/admin/integrations"),
      { wrapper }
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ enabled: false });
    act(() => {
      void result.current.refetch();
    });
    await waitFor(() => expect(result.current.data).toEqual({ enabled: true }));
    expect(apiRequestMock).toHaveBeenCalledTimes(2);
  });
});
