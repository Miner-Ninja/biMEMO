import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiRequest } from "@/services/bimemo-api";

const PRUNING_STALE_MS = 2 * 3600 * 1000; // Cycle runs hourly (scheduler.PRUNING_INTERVAL_SECONDS)
const BACKUP_STALE_MS = 25 * 3600 * 1000; // Backup runs daily

interface StatusIndicatorProps {
  label: string;
  value: string;
  color: "green" | "amber" | "red" | "neutral";
}

function StatusIndicator({ label, value, color }: StatusIndicatorProps) {
  const colorClass =
    color === "green"
      ? "bg-green-500"
      : color === "amber"
        ? "bg-amber-500"
        : color === "red"
          ? "bg-red-500"
          : "bg-gray-400";

  return (
    <div className="flex items-center gap-2 text-sm">
      <span className={`h-2.5 w-2.5 rounded-full ${colorClass}`} />
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}

export function MemoryStatusStrip({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
  const [indicators, setIndicators] = useState<StatusIndicatorProps[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadData() {
      try {
        const [health, coverage, diagnostics] = await Promise.all([
          apiRequest<{
            pipeline: { messages: number; records: number; assertions: number };
            zones: Record<string, unknown> | null;
            retrieval: Record<string, unknown>;
            graph: { record_queue_depth: number; last_assertion_at: string | null };
            loops: Record<string, string | null>;
          }>("/admin/memory/health"),
          apiRequest<{ total: number; embedded: number; unembedded: number }>(
            "/admin/memory/vector-coverage"
          ),
          apiRequest<{
            entries: Array<{ timestamp: string; decision: string }>;
          }>("/admin/memory/record-processing-diagnostics?limit=1"),
        ]);

        if (cancelled) return;

        const now = Date.now();
        const recEntry = diagnostics.entries?.[0];
        const recTime = recEntry?.timestamp
          ? new Date(recEntry.timestamp).getTime()
          : 0;
        const recAge = now - recTime;

        const recColor = !recEntry?.timestamp
          ? "neutral"
          : recAge < 24 * 3600 * 1000
            ? "green"
            : "amber";
        const recValue = recEntry?.timestamp
          ? new Date(recEntry.timestamp).toLocaleTimeString()
          : t("memoryDebug.lampNoData");

        const extLoop = health.loops?.["graph_extractor"];
        const extTime = extLoop ? new Date(extLoop).getTime() : 0;
        const extAge = now - extTime;
        const extColor = !extLoop
          ? "neutral"
          : extAge < 24 * 3600 * 1000
            ? "green"
            : "amber";
        const extValue = extLoop
          ? new Date(extLoop).toLocaleTimeString()
          : t("memoryDebug.lampNoData");

        const vecTotal = coverage.total;
        const vecUnembed = coverage.unembedded;
        const vecColor =
          vecTotal === 0 ? "neutral" : vecUnembed === 0 ? "green" : "amber";
        const vecValue =
          vecTotal === 0
            ? t("memoryDebug.lampNoData")
            : t("memoryDebug.lampVectorsCount", {
                embedded: coverage.embedded,
                total: coverage.total,
              });

        const pruneLoop = health.loops?.["pruning"];
        const pruneTime = pruneLoop ? new Date(pruneLoop).getTime() : 0;
        const pruneAge = now - pruneTime;
        const pruneColor = !pruneLoop
          ? "red"
          : pruneAge < PRUNING_STALE_MS
            ? "green"
            : "red";
        const pruneValue = pruneLoop
          ? new Date(pruneLoop).toLocaleTimeString()
          : t("memoryDebug.lampNoData");

        const backupLoop = health.loops?.["backup"];
        const backupTime = backupLoop ? new Date(backupLoop).getTime() : 0;
        const backupAge = now - backupTime;
        const backupColor = !backupLoop
          ? "red"
          : backupAge < BACKUP_STALE_MS
            ? "green"
            : "red";
        const backupValue = backupLoop
          ? new Date(backupLoop).toLocaleTimeString()
          : t("memoryDebug.lampNoData");

        setIndicators([
          {
            label: t("memoryDebug.lampRecording"),
            value: recValue,
            color: recColor,
          },
          {
            label: t("memoryDebug.lampExtraction"),
            value: extValue,
            color: extColor,
          },
          {
            label: t("memoryDebug.lampVectors"),
            value: vecValue,
            color: vecColor,
          },
          {
            label: t("memoryDebug.lampPruning"),
            value: pruneValue,
            color: pruneColor,
          },
          {
            label: t("memoryDebug.lampBackups"),
            value: backupValue,
            color: backupColor,
          },
        ]);
        setError(null);
      } catch {
        if (!cancelled) {
          setError(t("memoryDebug.lampLoadError"));
          setIndicators([
            {
              label: t("memoryDebug.lampRecording"),
              value: t("memoryDebug.lampNoData"),
              color: "neutral",
            },
            {
              label: t("memoryDebug.lampExtraction"),
              value: t("memoryDebug.lampNoData"),
              color: "neutral",
            },
            {
              label: t("memoryDebug.lampVectors"),
              value: t("memoryDebug.lampNoData"),
              color: "neutral",
            },
            {
              label: t("memoryDebug.lampPruning"),
              value: t("memoryDebug.lampNoData"),
              color: "neutral",
            },
            {
              label: t("memoryDebug.lampBackups"),
              value: t("memoryDebug.lampNoData"),
              color: "neutral",
            },
          ]);
        }
      }
    }

    loadData();
    return () => {
      cancelled = true;
    };
  }, [refreshToken, t]);

  return (
    <div className="flex flex-wrap gap-4 border-b border-border p-3">
      {indicators.map((ind) => (
        <StatusIndicator
          key={ind.label}
          label={ind.label}
          value={ind.value}
          color={ind.color}
        />
      ))}
      {error && <div className="text-xs text-red-500 ml-auto">{error}</div>}
    </div>
  );
}
