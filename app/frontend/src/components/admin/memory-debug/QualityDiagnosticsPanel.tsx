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

  const scoreTone =
    data.quality_score >= 85
      ? "text-emerald-400"
      : data.quality_score >= 65
        ? "text-amber-400"
        : "text-red-400";

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          title="Quality score"
          value={data.quality_score}
          detail="Memory health."
          toneClassName={scoreTone}
        />
        <SummaryCard
          title="Record provenance"
          value={`${Math.round(data.records.provenance_coverage * 100)}%`}
          detail={`${data.records.with_provenance}/${data.records.total} linked`}
          toneClassName="text-blue-400"
        />
        <SummaryCard
          title="Live summary support"
          value={`${Math.round(data.summaries.live_support_coverage * 100)}%`}
          detail={`${data.summaries.with_live_support}/${data.summaries.total} summaries still point to live records`}
          toneClassName="text-violet-400"
        />
        <SummaryCard
          title="Action items"
          value={`${Math.round(data.records.action_item_coverage * 100)}%`}
          detail={`${data.records.with_action_items}/${data.records.total} records`}
          toneClassName="text-orange-400"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Current quality issues</CardTitle>
          </CardHeader>
          <CardContent>
            {data.issues.length > 0 ? (
              <div className="space-y-2">
                {data.issues.map((issue) => (
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
            <CardTitle>Coverage details</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Key facts coverage</span>
              <span>{Math.round(data.records.key_fact_coverage * 100)}%</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Evidence coverage</span>
              <span>{Math.round(data.summaries.based_on_coverage * 100)}%</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Summaries with record links</span>
              <span>{Math.round(data.summaries.support_link_coverage * 100)}%</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Orphaned records</span>
              <span>{data.records.orphaned}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Orphaned summary links</span>
              <span>{data.summaries.orphaned_links}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                Duplicate summary main points
              </span>
              <span>{data.summaries.duplicate_main_points}</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
