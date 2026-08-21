import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  PipelineData,
  QualityData,
  RouteDiagnosticsSummaryData,
} from "@/features/memory/components/types";
import { PanelSkeleton } from "@/components/ui/skeletons";
import {
  EmptyStateCard,
  SummaryCard,
  AutoRefreshBadge,
} from "@/features/memory/components/shared";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/utils/utils";
import { apiRequest } from "@/services/bimemo-api";
import { buildAttentionItems } from "@/features/memory/components/quality-issues";
import { useMemoryEvents } from "@/features/memory/hooks/useMemoryEvents";

export function MemoryOverviewSection({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
  const [pipeline, setPipeline] = useState<PipelineData | null>(null);
  const [quality, setQuality] = useState<QualityData | null>(null);
  const [diagnostics, setDiagnostics] = useState<RouteDiagnosticsSummaryData | null>(
    null
  );
  const [error, setError] = useState("");
  const { events, connected } = useMemoryEvents(true);

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
        title={t("memoryDebug.overviewUnavailable")}
        description={t("memoryDebug.overviewLoadError", { error })}
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
      <div className="flex items-center justify-end gap-2">
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
            connected
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
              : "border-border bg-muted/40 text-muted-foreground"
          )}
        >
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              connected ? "animate-pulse bg-emerald-400" : "bg-muted-foreground"
            )}
          />
          {connected ? t("memoryDebug.liveConnected") : t("memoryDebug.liveOffline")}
        </span>
        <AutoRefreshBadge seconds={5} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t("memoryDebug.liveFeedTitle")}</CardTitle>
        </CardHeader>
        <CardContent>
          {events.length === 0 ? (
            <p className="font-prose text-sm text-muted-foreground">
              {t("memoryDebug.liveNoEvents")}
            </p>
          ) : (
            <div className="space-y-1">
              {events.map((event, index) => (
                <div
                  key={`${event.timestamp ?? ""}-${index}`}
                  className="flex items-center gap-2 text-xs"
                >
                  <Badge variant="secondary">{event.type}</Badge>
                  <span className="text-muted-foreground">
                    {event.timestamp
                      ? new Date(event.timestamp * 1000).toLocaleTimeString()
                      : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          title={t("memoryDebug.messages")}
          value={pipeline.messages}
          detail={t("memoryDebug.messagesDetail")}
          toneClassName="text-blue-400"
          accentClassName="bg-blue-400/80"
        />
        <SummaryCard
          title={t("memoryDebug.records")}
          value={pipeline.records}
          detail={t("memoryDebug.recordsDetail")}
          toneClassName="text-amber-400"
          accentClassName="bg-amber-400/80"
        />
        <SummaryCard
          title={t("memoryDebug.assertions")}
          value={pipeline.assertions}
          detail={t("memoryDebug.assertionsDetail")}
          toneClassName="text-violet-400"
          accentClassName="bg-violet-400/80"
        />
        <SummaryCard
          title={t("memoryDebug.qualityScore")}
          value={`${Math.round(quality.quality_score * 100)}%`}
          detail={attentionItems[0] ?? t("memoryDebug.qualityScoreDetail")}
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
            <CardTitle>{t("memoryDebug.attentionTitle")}</CardTitle>
            <CardDescription className="font-prose">
              {t("memoryDebug.attentionDescription")}
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
              <div className="font-prose rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3 text-sm text-emerald-300">
                {t("memoryDebug.noObviousProblems")}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t("memoryDebug.routingSnapshotTitle")}</CardTitle>
            <CardDescription className="font-prose">
              {t("memoryDebug.routingSnapshotDescription")}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-border bg-muted/20 p-3">
              <div className="text-2xl font-semibold text-blue-400">
                {diagnostics.totals.total_rows}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                {t("memoryDebug.storedRouteDecisions")}
              </div>
            </div>
            <div className="rounded-lg border border-border bg-muted/20 p-3">
              <div className="text-2xl font-semibold text-amber-400">
                {diagnostics.totals.fallback_rows}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                {t("memoryDebug.fallbackUses")}
              </div>
            </div>
            <div className="rounded-lg border border-border bg-muted/20 p-3">
              <div className="text-2xl font-semibold text-orange-400">
                {diagnostics.totals.mismatch_rows}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                {t("memoryDebug.routeChanges")}
              </div>
            </div>
            <div className="rounded-lg border border-border bg-muted/20 p-3">
              <div className="text-2xl font-semibold text-violet-400">
                {diagnostics.totals.long_term_rows}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                {t("memoryDebug.longTermModeUses")}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("memoryDebug.memoryLinkCoverageTitle")}</CardTitle>
            <CardDescription className="font-prose">
              {t("memoryDebug.memoryLinkCoverageDescription")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                {t("memoryDebug.validAssertions")}
              </span>
              <span>{quality.assertions_valid}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                {t("memoryDebug.invalidatedAssertions")}
              </span>
              <span>{quality.assertions_invalidated}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                {t("memoryDebug.assertionsCitingPruned")}
              </span>
              <span>{quality.assertions_with_missing_evidence}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                {t("memoryDebug.orphanEntities")}
              </span>
              <span>{quality.orphan_entities}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                {t("memoryDebug.duplicateAssertionTexts")}
              </span>
              <span>{quality.duplicate_assertion_texts}</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
