import { useEffect, useState } from "react";

import type {
  PipelineData,
  QualityData,
  RouteDiagnosticsSummaryData,
} from "@/components/admin/memory-debug/types";
import { PanelSkeleton } from "@/components/ui/skeletons";
import { EmptyStateCard, SummaryCard } from "@/components/admin/memory-debug/shared";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { apiRequest } from "@/lib/bimemo-api";

function buildAttentionItems(
  quality: QualityData | null,
  diagnostics: RouteDiagnosticsSummaryData | null
): string[] {
  const items: string[] = [];

  if (quality) {
    if (quality.orphan_entities > 0) {
      items.push(`${quality.orphan_entities} graph entities have no assertions yet.`);
    }
    if (quality.assertions_with_missing_evidence > 0) {
      items.push(
        `${quality.assertions_with_missing_evidence} assertions cite records that were already pruned.`
      );
    }
    if (quality.duplicate_assertion_texts > 0) {
      items.push(
        `${quality.duplicate_assertion_texts} assertions repeat the same statement text.`
      );
    }
  }

  if (diagnostics) {
    if (diagnostics.totals.mismatch_rows > 0) {
      items.push(
        `${diagnostics.totals.mismatch_rows} recent route decisions changed after deeper analysis.`
      );
    }
    if (diagnostics.totals.fallback_rows > 0) {
      items.push(
        `${diagnostics.totals.fallback_rows} recent questions needed the backup route classifier.`
      );
    }
  }

  return items.slice(0, 5);
}

export function MemoryOverviewSection({ refreshToken }: { refreshToken: number }) {
  const [pipeline, setPipeline] = useState<PipelineData | null>(null);
  const [quality, setQuality] = useState<QualityData | null>(null);
  const [diagnostics, setDiagnostics] = useState<RouteDiagnosticsSummaryData | null>(
    null
  );
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;

    async function loadOverview(): Promise<void> {
      try {
        const [pipelineResult, qualityResult, diagnosticsResult] = await Promise.all([
          apiRequest<PipelineData>("/admin/memory/pipeline"),
          apiRequest<QualityData>("/admin/memory/quality"),
          apiRequest<RouteDiagnosticsSummaryData>(
            "/admin/memory/route-diagnostics/summary?recent_hours=24"
          ),
        ]);
        if (!active) return;
        setPipeline(pipelineResult);
        setQuality(qualityResult);
        setDiagnostics(diagnosticsResult);
        setError("");
      } catch (requestError) {
        if (!active) return;
        setError(String(requestError));
      }
    }

    loadOverview();
    const intervalId = window.setInterval(loadOverview, 5000);

    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [refreshToken]);

  if (error) {
    return (
      <EmptyStateCard
        title="Overview unavailable"
        description={`The overview could not be loaded: ${error}`}
      />
    );
  }

  if (!pipeline || !quality || !diagnostics) {
    return <PanelSkeleton />;
  }

  const attentionItems = buildAttentionItems(quality, diagnostics);
  const qualityTone =
    quality.quality_score * 100 >= 85
      ? "text-emerald-400"
      : quality.quality_score * 100 >= 65
        ? "text-amber-400"
        : "text-red-400";

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          title="Messages"
          value={pipeline.messages}
          detail="Raw conversation entries currently stored."
          toneClassName="text-blue-400"
          accentClassName="bg-blue-400/80"
        />
        <SummaryCard
          title="Records"
          value={pipeline.records}
          detail="Compressed memory records extracted from messages."
          toneClassName="text-amber-400"
          accentClassName="bg-amber-400/80"
        />
        <SummaryCard
          title="Assertions"
          value={pipeline.assertions}
          detail="Graph knowledge assertions extracted from records."
          toneClassName="text-violet-400"
          accentClassName="bg-violet-400/80"
        />
        <SummaryCard
          title="Quality score"
          value={`${Math.round(quality.quality_score * 100)}%`}
          detail="Overall memory health based on links and coverage."
          toneClassName={qualityTone}
          accentClassName={
            quality.quality_score * 100 >= 85
              ? "bg-emerald-400/80"
              : quality.quality_score * 100 >= 65
                ? "bg-amber-400/80"
                : "bg-red-400/80"
          }
        />
      </div>

      <div className="grid gap-4">
        <Card>
          <CardHeader>
            <CardTitle>What needs attention now</CardTitle>
            <CardDescription>
              The most important signals from the current memory snapshot and the last
              24 hours of diagnostics.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {attentionItems.length > 0 ? (
              <div className="space-y-2">
                {attentionItems.map((item) => (
                  <div
                    key={item}
                    className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-sm text-amber-700 dark:text-amber-200"
                  >
                    {item}
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3 text-sm text-emerald-300">
                No obvious problems are visible in the current overview window.
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Routing snapshot</CardTitle>
            <CardDescription>
              Recent question-routing behavior from the last 24 hours.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-border bg-muted/20 p-3">
              <div className="text-2xl font-semibold text-blue-400">
                {diagnostics.totals.total_rows}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                Stored route decisions
              </div>
            </div>
            <div className="rounded-lg border border-border bg-muted/20 p-3">
              <div className="text-2xl font-semibold text-amber-400">
                {diagnostics.totals.fallback_rows}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">Fallback uses</div>
            </div>
            <div className="rounded-lg border border-border bg-muted/20 p-3">
              <div className="text-2xl font-semibold text-orange-400">
                {diagnostics.totals.mismatch_rows}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">Route changes</div>
            </div>
            <div className="rounded-lg border border-border bg-muted/20 p-3">
              <div className="text-2xl font-semibold text-violet-400">
                {diagnostics.totals.long_term_rows}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                Long-term mode uses
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Memory link coverage</CardTitle>
            <CardDescription>
              How well stored memory items are tied back to supporting data.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Valid assertions</span>
              <span>{quality.assertions_valid}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Invalidated assertions</span>
              <span>{quality.assertions_invalidated}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                Assertions citing pruned records
              </span>
              <span>{quality.assertions_with_missing_evidence}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Orphan entities</span>
              <span>{quality.orphan_entities}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Duplicate assertion texts</span>
              <span>{quality.duplicate_assertion_texts}</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
