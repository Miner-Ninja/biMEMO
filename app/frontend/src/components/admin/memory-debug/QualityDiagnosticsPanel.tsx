import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { PanelSkeleton } from "@/components/ui/skeletons";
import type { QualityData } from "@/components/admin/memory-debug/types";
import { SummaryCard } from "@/components/admin/memory-debug/shared";
import { buildQualityIssues } from "@/components/admin/memory-debug/quality-issues";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiRequest } from "@/lib/bimemo-api";

export function QualityDiagnosticsPanel({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
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
        {t("memoryDebug.qualityLoadError", { error })}
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

  const issues = buildQualityIssues(data);

  return (
    <div className="space-y-6">
      {/* The score and its reasons sit side by side (audit: the big
          number appeared without any "why" next to it). */}
      <div className="grid gap-4 lg:grid-cols-[minmax(220px,1fr)_2fr]">
        <SummaryCard
          title={t("memoryDebug.qualityScore")}
          value={`${scorePercent}%`}
          detail={t("memoryDebug.graphMemoryHealth")}
          toneClassName={scoreTone}
        />
        <Card>
          <CardHeader>
            <CardTitle>{t("memoryDebug.issuesTitle")}</CardTitle>
          </CardHeader>
          <CardContent>
            {issues.length > 0 ? (
              <div className="space-y-2">
                {issues.map((issue) => (
                  <div
                    key={issue}
                    className="font-prose rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-sm text-amber-700 dark:text-amber-200"
                  >
                    {issue}
                  </div>
                ))}
              </div>
            ) : (
              <div className="font-prose rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3 text-sm text-emerald-700 dark:text-emerald-300">
                {t("memoryDebug.noIssues")}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <SummaryCard
          title={t("memoryDebug.validAssertions")}
          value={data.assertions_valid}
          detail={t("memoryDebug.invalidatedKeptAsHistory", {
            count: data.assertions_invalidated,
          })}
          toneClassName="text-violet-400"
        />
        <SummaryCard
          title={t("memoryDebug.evidenceIntegrity")}
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
          detail={t("memoryDebug.evidenceIntegrityDetail")}
          toneClassName="text-blue-400"
        />
        <SummaryCard
          title={t("memoryDebug.orphanEntities")}
          value={data.orphan_entities}
          detail={t("memoryDebug.orphanEntitiesDetail")}
          toneClassName="text-orange-400"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>{t("memoryDebug.tierCounts")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">{t("memoryDebug.messages")}</span>
              <span>{data.counts.message}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">{t("memoryDebug.records")}</span>
              <span>{data.counts.record}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                {t("memoryDebug.assertions")}
              </span>
              <span>{data.counts.assertion}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                {t("memoryDebug.assertionsWithoutEvidence")}
              </span>
              <span>{data.assertions_without_evidence}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                {t("memoryDebug.duplicateAssertionTexts")}
              </span>
              <span>{data.duplicate_assertion_texts}</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
