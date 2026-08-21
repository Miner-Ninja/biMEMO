/**
 * Tests for the shared cache contract (R4, R8):
 * - navigating between pages keeps cached data: a page is an observer
 *   of the app-level cache, so unmounting it (and mounting it again
 *   within the freshness window) issues no new request;
 * - a successful mutation invalidates its collection (the next read is
 *   a fresh request reflecting the post-mutation server state);
 * - a failed mutation leaves the cached data untouched.
 */
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
} from "@tanstack/react-query";

vi.mock("@/services/bimemo-api", () => ({ apiRequest: vi.fn() }));

import { apiRequest } from "@/services/bimemo-api";

const apiRequestMock = vi.mocked(apiRequest);

let queryClient: QueryClient;

function provider({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

/** A page stand-in: one query observer for one endpoint. */
function PageObserver({ endpoint }: { endpoint: string }) {
  const { data, isInitialLoading } = useQuery({
    queryKey: [endpoint],
    queryFn: () => apiRequest<{ value: number }>(endpoint),
  });
  return (
    <span data-testid="observer">
      {isInitialLoading ? "loading" : String(data?.value)}
    </span>
  );
}

/** Collection query + mutation: the exact invalidate-on-success pattern
 * the migrated admin pages use. */
function useCollection() {
  const { data, isInitialLoading } = useQuery({
    queryKey: ["collection"],
    queryFn: () => apiRequest<{ value: number }>("/list"),
  });
  const mutation = useMutation({
    mutationFn: () => apiRequest<{ ok: boolean }>("/mutate"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["collection"] });
    },
  });
  return {
    data,
    isInitialLoading,
    mutate: () => void mutation.mutate(),
  };
}

describe("shared query cache", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: 30_000 },
      },
    });
  });

  it("S8.1 serves the page from cache after navigating away and back", async () => {
    apiRequestMock.mockResolvedValue({ value: 42 });
    const away = render(<PageObserver endpoint="/resource-x" />, {
      wrapper: provider,
    });
    await screen.findByText("42");
    away.unmount();
    const back = render(<PageObserver endpoint="/resource-x" />, {
      wrapper: provider,
    });
    await screen.findByText("42");
    // The second mount is served from the shared cache: exactly one
    // network request for the resource in total.
    expect(apiRequestMock).toHaveBeenCalledTimes(1);
    back.unmount();
  });

  it("S4.1 a successful mutation invalidates the collection", async () => {
    let serverValue = 1;
    apiRequestMock.mockImplementation(async (endpoint: string) => {
      if (endpoint === "/mutate") {
        serverValue = 2; // the mutation changes the server state
        return { ok: true };
      }
      return { value: serverValue };
    });
    const { result } = renderHook(() => useCollection(), { wrapper: provider });
    await waitFor(() => expect(result.current.data).toEqual({ value: 1 }));
    act(() => {
      result.current.mutate();
    });
    await waitFor(() => expect(result.current.data).toEqual({ value: 2 }));
    // initial list, the mutation, and the invalidation-triggered refetch
    expect(apiRequestMock).toHaveBeenCalledTimes(3);
  });

  it("S4.2 a failed mutation keeps the cached data", async () => {
    apiRequestMock.mockImplementation(async (endpoint: string) => {
      if (endpoint === "/mutate") {
        throw new Error("server rejected the mutation");
      }
      return { value: 1 };
    });
    const { result } = renderHook(() => useCollection(), { wrapper: provider });
    await waitFor(() => expect(result.current.data).toEqual({ value: 1 }));
    act(() => {
      result.current.mutate();
    });
    // Give any (incorrect) invalidation-triggered refetch time to land.
    const { promise: settle, resolve } = Promise.withResolvers<void>();
    await act(async () => {
      setTimeout(resolve, 100);
      await settle;
    });
    // Only the initial list and the failed mutation were issued; no
    // refetch followed the failure.
    expect(apiRequestMock).toHaveBeenCalledTimes(2);
    expect(result.current.data).toEqual({ value: 1 });
  });
});
