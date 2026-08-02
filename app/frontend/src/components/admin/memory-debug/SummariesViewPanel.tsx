import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AdminCardSkeleton } from "@/components/ui/skeletons";
import { ErrorAlert } from "@/components/ui/error-alert";
import { apiRequest } from "@/lib/bimemo-api";

interface Summary {
  main_point: string;
  trust_level: string;
  based_on_count: number;
  created_at: string;
}

const PAGE_SIZE = 20;

function trustLevelColor(trustLevel: string): string {
  if (trustLevel === "high") return "text-green-600 dark:text-green-400";
  if (trustLevel === "medium") return "text-amber-600 dark:text-amber-400";
  return "text-muted-foreground";
}

export function SummariesViewPanel({ refreshToken }: { refreshToken: number }) {
  const [summaries, setSummaries] = useState<Summary[]>([]);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const hasNextPage = summaries.length === PAGE_SIZE;

  const fetchSummaries = useCallback(async (p: number) => {
    setLoading(true);
    try {
      const data = await apiRequest<Summary[]>(
        `/admin/summaries?page=${p}&per_page=${PAGE_SIZE}`
      );
      setSummaries(data);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load summaries");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setPage(1);
    fetchSummaries(1);
  }, [refreshToken, fetchSummaries]);

  useEffect(() => {
    fetchSummaries(page);
  }, [page, fetchSummaries]);

  return (
    <div className="space-y-4">
      <ErrorAlert error={error} />

      {loading ? (
        <AdminCardSkeleton count={5} />
      ) : summaries.length === 0 ? (
        <p className="text-sm text-muted-foreground">No summaries yet.</p>
      ) : (
        <>
          <div className="space-y-2">
            {summaries.map((r) => (
              <div
                key={`${r.created_at}-${r.main_point}`}
                className="rounded-lg border border-border p-4 space-y-1"
              >
                <p className="font-medium text-sm">{r.main_point}</p>
                <div className="flex flex-wrap gap-3 text-xs">
                  <span className={trustLevelColor(r.trust_level)}>
                    Trust: {r.trust_level}
                  </span>
                  <span className="text-muted-foreground">
                    Based on: {r.based_on_count}
                  </span>
                  <span className="text-muted-foreground">
                    {new Date(r.created_at).toLocaleDateString()}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {(page > 1 || hasNextPage) && (
            <div className="flex items-center justify-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                aria-label="Previous page"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="text-sm text-muted-foreground">Page {page}</span>
              <Button
                variant="ghost"
                size="icon"
                disabled={!hasNextPage}
                onClick={() => setPage((p) => p + 1)}
                aria-label="Next page"
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
