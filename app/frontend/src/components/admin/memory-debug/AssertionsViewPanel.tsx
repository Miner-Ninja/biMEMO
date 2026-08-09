import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { AdminCardSkeleton } from "@/components/ui/skeletons";
import { ErrorAlert } from "@/components/ui/error-alert";
import { AssertionCard } from "@/components/admin/memory-debug/AssertionCard";
import type { AssertionEntry } from "@/components/admin/memory-debug/types";
import { apiRequest } from "@/lib/bimemo-api";

const PAGE_SIZE = 20;

export function AssertionsViewPanel({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
  const [assertions, setAssertions] = useState<AssertionEntry[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchAssertions = useCallback(
    async (search: string) => {
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
        setError(
          err instanceof Error ? err.message : t("memoryDebug.assertionsLoadFailed")
        );
      } finally {
        setLoading(false);
      }
    },
    [t]
  );

  useEffect(() => {
    fetchAssertions(query);
  }, [refreshToken, query, fetchAssertions]);

  return (
    <div className="space-y-4">
      <ErrorAlert error={error} />
      <Input
        placeholder={t("memoryDebug.searchAssertionsPlaceholder")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />

      {loading ? (
        <AdminCardSkeleton count={5} />
      ) : assertions.length === 0 ? (
        <p className="font-prose text-sm text-muted-foreground">
          {t("memoryDebug.noGraphAssertions")}
        </p>
      ) : (
        <ul className="space-y-2">
          {assertions.map((assertion) => (
            <li key={assertion.id} className="rounded-md border p-3">
              <AssertionCard
                data={{
                  id: assertion.id,
                  text: assertion.assertion_text ?? assertion.text ?? "",
                  relation: assertion.relation,
                  trustLevel: assertion.trust_level,
                  validFrom: assertion.valid_from,
                  invalidFrom: assertion.invalid_from,
                  createdAt: assertion.created_at,
                }}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
