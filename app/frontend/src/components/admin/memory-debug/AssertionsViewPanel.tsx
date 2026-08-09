import { useCallback, useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { AdminCardSkeleton } from "@/components/ui/skeletons";
import { ErrorAlert } from "@/components/ui/error-alert";
import { apiRequest } from "@/lib/bimemo-api";

interface AssertionEntry {
  id: string;
  text?: string;
  assertion_text?: string;
  relation?: string;
  trust_level?: string;
  valid_from?: string | null;
  invalid_from?: string | null;
  created_at?: string;
}

const PAGE_SIZE = 20;

function trustLevelColor(trustLevel: string | undefined): string {
  if (trustLevel === "high") return "text-green-600 dark:text-green-400";
  if (trustLevel === "medium") return "text-amber-600 dark:text-amber-400";
  return "text-muted-foreground";
}

export function AssertionsViewPanel({ refreshToken }: { refreshToken: number }) {
  const [assertions, setAssertions] = useState<AssertionEntry[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchAssertions = useCallback(async (search: string) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
      if (search) params.set("query", search);
      const data = await apiRequest<{ assertions: AssertionEntry[] }>(
        `/admin/memory/assertions?${params.toString()}`
      );
      setAssertions(data.assertions);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load assertions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAssertions(query);
  }, [refreshToken, query, fetchAssertions]);

  return (
    <div className="space-y-4">
      <ErrorAlert error={error} />
      <Input
        placeholder="Search graph assertions…"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />

      {loading ? (
        <AdminCardSkeleton count={5} />
      ) : assertions.length === 0 ? (
        <p className="text-sm text-muted-foreground">No graph assertions yet.</p>
      ) : (
        <ul className="space-y-2">
          {assertions.map((assertion) => (
            <li key={assertion.id} className="rounded-md border p-3 text-sm space-y-1">
              <div>{assertion.assertion_text ?? assertion.text}</div>
              <div className="flex gap-3 text-xs text-muted-foreground">
                {assertion.relation ? <span>({assertion.relation})</span> : null}
                {assertion.trust_level ? (
                  <span className={trustLevelColor(assertion.trust_level)}>
                    {assertion.trust_level}
                  </span>
                ) : null}
                <span>
                  {(assertion.valid_from ?? assertion.created_at ?? "").slice(0, 10)}
                </span>
                {assertion.invalid_from ? (
                  <span className="text-red-500">
                    invalidated {assertion.invalid_from.slice(0, 10)}
                  </span>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
