/**
 * Shared QueryClient for the web UI's server-state cache.
 *
 * Freshness: a value is fresh for 30 seconds (a security-first app
 * re-reads server state quickly; window focus forces an immediate
 * re-read of anything stale). `retry: 1` absorbs a single transient
 * network blip. `gcTime` of 5 minutes: leaving a page and returning
 * within 5 minutes shows the cached value instantly.
 */
import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: true,
      gcTime: 300_000,
    },
  },
});
