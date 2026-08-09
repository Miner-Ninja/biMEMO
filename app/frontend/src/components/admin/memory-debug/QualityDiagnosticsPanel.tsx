import { useEffect, useState } from "react";

import { PanelSkeleton } from "@/components/ui/skeletons";
import type { QualityData } from "@/components/admin/memory-debug/types";
import { SummaryCard } from "@/components/admin/memory-debug/shared";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiRequest } from "@/lib/bimemo-api";

export function QualityDiagnosticsPanel({ refreshToken }: { refreshToken: number }) {
  const [data, setData] = useState<QualityData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiRequest<QualityData>("/admin/memory/quality")
      .then(setData)
      .catch((requestError) => setError(String(requestError)));
  }, [refreshToken]);

  if (error)
    return (
      <div className="p-4 text-sm text-red-400">
        Quality diagnostics could not be loaded: {error}
      </div>
    );
  if (!data) return <PanelSkeleton />;

  const scorePercent = Math.round(data.quality_score * 100);
  const scoreTone =
    scorePercent >= 85
      ? "text-emerald-400"
      : scorePercent >= 65
        ? "text-amber-400"
        : "text-red-400";

  const issues: string[] = [];
  if (data.assertions_with_missing_evidence > 0) {
    issues.push(
      `${data.assertions_with_missing_evidence} valid assertions cite records that were already pruned.`
    );
  }
  if (data.assertions_without_evidence > 0) {
    issues.push(
      `${data.assertions_without_evidence} valid assertions carry no evidence records at all.`
    );
  }
  if (data.duplicate_assertion_texts > 0) {
    issues.push(
      `${data.duplicate_assertion_texts} assertions repeat the same statement text.`
    );
  }
  if (data.orphan_entities > 0) {
    issues.push(`${data.orphan_entities} entities have no assertions in either role.`);
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          title="Quality score"
          value={`${scorePercent}%`}
          detail="Graph memory health."
          toneClassName={scoreTone}
        />
        <SummaryCard
          title="Valid assertions"
          value={data.assertions_valid}
          detail={`${data.assertions_invalidated} invalidated kept as history`}
          toneClassName="text-violet-400"
        />
        <SummaryCard
          title="Evidence integrity"
          value={
            data.assertions_valid > 0
              ? `${Math.round(
                  ((data.assertions_valid -
                    data.assertions_with_missing_evidence -
                    data.assertions_without_evidence) /
                    data.assertions_valid) *
                    100
                )}%`
              : "—"
          }
          detail="Valid assertions whose cited records still exist."
          toneClassName="text-blue-400"
        />
        <SummaryCard
          title="Orphan entities"
          value={data.orphan_entities}
          detail="Nodes without any assertions (pruned after 24h)."
          toneClassName="text-orange-400"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Current quality issues</CardTitle>
          </CardHeader>
          <CardContent>
            {issues.length > 0 ? (
              <div className="space-y-2">
                {issues.map((issue) => (
                  <div
                    key={issue}
                    className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-sm text-amber-200"
                  >
                    {issue}
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3 text-sm text-emerald-300">
                No major quality issues are visible in the current snapshot.
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Tier counts</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Messages</span>
              <span>{data.counts.message}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Records</span>
              <span>{data.counts.record}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Assertions</span>
              <span>{data.counts.assertion}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Assertions without evidence</span>
              <span>{data.assertions_without_evidence}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Duplicate assertion texts</span>
              <span>{data.duplicate_assertion_texts}</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
