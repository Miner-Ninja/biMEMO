import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiRequest } from "@/services/bimemo-api";
import { humanizeRecorderDecision } from "./humanize";

const PRUNING_STALE_MS = 2 * 3600 * 1000;
const BACKUP_STALE_MS = 25 * 3600 * 1000;

const BAD_DECISIONS = ["llm_error", "parse_failed", "unsafe_output"];

interface Problem {
  text: string;
  view?: "quality" | "audit" | "recorder";
}

export function ProblemsPanel({
  refreshToken,
  onOpenDetail,
}: {
  refreshToken: number;
  onOpenDetail?: (view: string) => void;
}) {
  const { t } = useTranslation();
  const [problems, setProblems] = useState<Problem[]>([]);

  useEffect(() => {
    let cancelled = false;

    async function loadData() {
      try {
        const [health, quality, recorder] = await Promise.all([
          apiRequest<{
            loops: Record<string, string | null>;
          }>("/admin/memory/health"),
          apiRequest<{
            assertions_with_missing_evidence: number;
            duplicate_assertion_texts: number;
            orphan_entities: number;
          }>("/admin/memory/quality"),
          apiRequest<{
            entries: Array<{ timestamp: string; decision: string }>;
          }>("/admin/memory/record-processing-diagnostics?limit=50"),
        ]);

        if (cancelled) return;

        const results: Problem[] = [];
        const now = Date.now();

        const pruneLoop = health.loops?.["pruning"];
        if (!pruneLoop) {
          results.push({
            text: t("memoryDebug.problemLoopNever", {
              name: t("memoryDebug.lampPruning"),
            }),
            view: "quality",
          });
        } else if (now - new Date(pruneLoop).getTime() >= PRUNING_STALE_MS) {
          results.push({
            text: t("memoryDebug.problemLoopStale", {
              name: t("memoryDebug.lampPruning"),
              time: new Date(pruneLoop).toLocaleTimeString(),
            }),
            view: "quality",
          });
        }

        const backupLoop = health.loops?.["backup"];
        if (!backupLoop) {
          results.push({
            text: t("memoryDebug.problemLoopNever", {
              name: t("memoryDebug.lampBackups"),
            }),
            view: "quality",
          });
        } else if (now - new Date(backupLoop).getTime() >= BACKUP_STALE_MS) {
          results.push({
            text: t("memoryDebug.problemLoopStale", {
              name: t("memoryDebug.lampBackups"),
              time: new Date(backupLoop).toLocaleTimeString(),
            }),
            view: "quality",
          });
        }

        if (quality.assertions_with_missing_evidence > 0) {
          results.push({
            text: t("memoryDebug.problemMissingEvidence", {
              count: quality.assertions_with_missing_evidence,
            }),
            view: "quality",
          });
        }
        if (quality.duplicate_assertion_texts > 0) {
          results.push({
            text: t("memoryDebug.problemDuplicates", {
              count: quality.duplicate_assertion_texts,
            }),
            view: "quality",
          });
        }
        if (quality.orphan_entities > 0) {
          results.push({
            text: t("memoryDebug.problemOrphans", {
              count: quality.orphan_entities,
            }),
            view: "quality",
          });
        }

        for (const entry of recorder.entries ?? []) {
          if (BAD_DECISIONS.includes(entry.decision)) {
            const humanized = humanizeRecorderDecision(t, entry.decision);
            results.push({
              text: `${new Date(entry.timestamp).toLocaleTimeString()} ${humanized.text}`,
              view: "recorder",
            });
          }
        }

        setProblems(results);
      } catch {
        if (!cancelled) setProblems([]);
      }
    }

    loadData();
    return () => {
      cancelled = true;
    };
  }, [refreshToken, t]);

  if (problems.length === 0) {
    return <div className="p-4 text-green-600">{t("memoryDebug.noProblems")}</div>;
  }

  return (
    <div className="space-y-2 p-4">
      {problems.map((p, i) => (
        <div key={i} className="flex items-center justify-between text-sm text-red-600">
          <span>{p.text}</span>
          {p.view && onOpenDetail && (
            <button
              onClick={() => onOpenDetail(p.view!)}
              className="text-blue-600 underline"
            >
              {t("memoryDebug.problemDetails")}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
