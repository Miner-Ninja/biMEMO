import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiRequest } from "@/services/bimemo-api";
import { errorToDisplayText } from "@/hooks/use-server-state";

/**
 * Cached server-state hook for admin pages.
 *
 * Backed by the app-wide query cache: a value is fresh for 30
 * seconds, is shared across pages, and is re-read when the window
 * regains focus. The public shape (data, loading, error, setError,
 * refetch) is unchanged, so the existing consumer needs no edits.
 * `error` is a display string: a manually set error (after a failed
 * mutation) wins, otherwise the query's failure goes through the
 * closed display mapping.
 */
export function useAdminData<T>(endpoint: string) {
  const { t } = useTranslation();
  const [manualError, setManualError] = useState("");
  const { data, isInitialLoading, error, refetch } = useQuery({
    queryKey: [endpoint],
    queryFn: () => apiRequest<T>(endpoint),
  });
  const displayError = manualError || (error ? errorToDisplayText(error, t) : "");
  return {
    data: (data ?? null) as T | null,
    loading: isInitialLoading,
    error: displayError,
    setError: setManualError,
    refetch,
  };
}
