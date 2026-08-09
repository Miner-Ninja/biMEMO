import { useCallback, useEffect, useState } from "react";

import { PanelSkeleton } from "@/components/ui/skeletons";
import type {
  RouteDiagnosticEntry,
  RouteDiagnosticsSummaryData,
} from "@/components/admin/memory-debug/types";
import {
  RouteMismatchBadge,
  SummaryCard,
} from "@/components/admin/memory-debug/shared";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiRequest } from "@/lib/bimemo-api";

function buildTopProblems(
  summary: RouteDiagnosticsSummaryData
): Array<{ label: string; count: number; toneClassName: string }> {
  const problems: Array<{ label: string; count: number; toneClassName: string }> = [];
  const topMismatch = summary.mismatch_labels.find(
    (item) => item.label !== "no_mismatch"
  );
  if (topMismatch) {
    problems.push({
      label: `Top route change: ${topMismatch.label.replace(/_/g, " ")}`,
      count: topMismatch.count,
      toneClassName: "text-orange-400",
    });
  }
  const topFallbackReason = summary.fallback_reasons[0];
  if (topFallbackReason) {
    problems.push({
      label: `Top fallback reason: ${topFallbackReason.reason}`,
      count: topFallbackReason.count,
      toneClassName: "text-amber-400",
    });
  }
  const fallbackWithoutChange = Math.max(
    0,
    summary.totals.fallback_rows - summary.totals.mismatch_rows
  );
  if (fallbackWithoutChange > 0) {
    problems.push({
      label: "Fallback ran without changing the route",
      count: fallbackWithoutChange,
      toneClassName: "text-sky-400",
    });
  }
  return problems;
}

export function RoutingDiagnosticsPanel({ refreshToken }: { refreshToken: number }) {
  const [summary, setSummary] = useState<RouteDiagnosticsSummaryData | null>(null);
  const [entries, setEntries] = useState<RouteDiagnosticEntry[]>([]);
  const [recentHours, setRecentHours] = useState<number | null>(24);
  const [error, setError] = useState("");

  const fetchData = useCallback(async (): Promise<void> => {
    try {
      const summaryPath =
        recentHours == null
          ? "/admin/memory/route-diagnostics/summary"
          : `/admin/memory/route-diagnostics/summary?recent_hours=${recentHours}`;
      const [summaryResult, entriesResult] = await Promise.all([
        apiRequest<RouteDiagnosticsSummaryData>(summaryPath),
        apiRequest<{ diagnostics: RouteDiagnosticEntry[] }>(
          "/admin/memory/route-diagnostics?limit=20"
        ),
      ]);
      setSummary(summaryResult);
      setEntries(entriesResult.diagnostics || []);
      setError("");
    } catch (requestError) {
      setError(String(requestError));
    }
  }, [recentHours]);

  useEffect(() => {
    fetchData();
    const intervalId = window.setInterval(fetchData, 5000);
    return () => window.clearInterval(intervalId);
  }, [fetchData, refreshToken]);

  if (error) {
    return (
      <div className="p-4 text-sm text-red-400">
        Routing diagnostics could not be loaded: {error}
      </div>
    );
  }
  if (!summary) {
    return <PanelSkeleton />;
  }

  const topProblems = buildTopProblems(summary);

  const exportSummaryJson = (): void => {
    const payload = {
      exported_at: new Date().toISOString(),
      summary_window:
        recentHours == null ? "all_retained" : `last_${recentHours}_hours`,
      summary,
      top_current_problems: topProblems.map((problem) => ({
        label: problem.label,
        count: problem.count,
      })),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `memory-route-diagnostics-${payload.summary_window}-${Date.now()}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        {[
          { label: "Last hour", value: 1 },
          { label: "Last 24h", value: 24 },
          { label: "Last 7d", value: 168 },
          { label: "All retained", value: null as number | null },
        ].map((option) => (
          <Button
            key={option.label}
            size="sm"
            variant={recentHours === option.value ? "default" : "outline"}
            onClick={() => setRecentHours(option.value)}
          >
            {option.label}
          </Button>
        ))}
        <Button size="sm" variant="outline" onClick={exportSummaryJson}>
          Export JSON
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          title="Stored decisions"
          value={summary.totals.total_rows}
          toneClassName="text-blue-400"
        />
        <SummaryCard
          title="Fallback used"
          value={summary.totals.fallback_rows}
          toneClassName="text-amber-400"
        />
        <SummaryCard
          title="Route changed"
          value={summary.totals.mismatch_rows}
          toneClassName="text-orange-400"
        />
        <SummaryCard
          title="Long-term mode"
          value={summary.totals.long_term_rows}
          toneClassName="text-violet-400"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Top current problems</CardTitle>
        </CardHeader>
        <CardContent>
          {topProblems.length > 0 ? (
            <div className="grid gap-3 lg:grid-cols-3">
              {topProblems.map((problem) => (
                <div
                  key={problem.label}
                  className="rounded-lg border border-border bg-muted/20 p-3"
                >
                  <div className={`text-2xl font-semibold ${problem.toneClassName}`}>
                    {problem.count}
                  </div>
                  <div className="mt-1 text-sm">{problem.label}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3 text-sm text-emerald-700 dark:text-emerald-300">
              No clear routing problems are visible in this time window.
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Route changes</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {summary.mismatch_labels.length > 0 ? (
              summary.mismatch_labels.map((item) => (
                <div
                  key={item.label}
                  className="flex items-center justify-between gap-3 text-sm"
                >
                  <RouteMismatchBadge label={item.label} />
                  <span>{item.count}</span>
                </div>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                No route changes recorded.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Final route types</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {summary.final_intents.length > 0 ? (
              summary.final_intents.map((item) => (
                <div
                  key={item.intent}
                  className="flex items-center justify-between gap-3 text-sm"
                >
                  <span>{item.intent}</span>
                  <span>{item.count}</span>
                </div>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">No route data recorded.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Fallback reasons</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {summary.fallback_reasons.length > 0 ? (
              summary.fallback_reasons.map((item) => (
                <div
                  key={item.reason}
                  className="flex items-center justify-between gap-3 text-sm"
                >
                  <span>{item.reason}</span>
                  <span>{item.count}</span>
                </div>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                No fallback activity recorded.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent route decisions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {entries.map((entry) => (
            <Collapsible
              key={entry.id}
              className="rounded-lg border border-border bg-muted/10"
            >
              <CollapsibleTrigger className="w-full p-4 text-left">
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <RouteMismatchBadge label={entry.mismatch_label} />
                    {entry.fallback.attempted ? (
                      <span className="inline-flex items-center rounded-md border border-amber-500/20 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-400">
                        fallback used
                      </span>
                    ) : null}
                    {entry.used_long_term_mode ? (
                      <span className="inline-flex items-center rounded-md border border-violet-500/20 bg-violet-500/10 px-2 py-0.5 text-xs font-medium text-violet-400">
                        long-term mode
                      </span>
                    ) : null}
                    <span className="ml-auto text-xs text-muted-foreground">
                      {entry.created_at}
                    </span>
                  </div>
                  <div className="grid gap-2 md:grid-cols-3">
                    <div className="rounded-lg border border-border bg-background p-3">
                      <div className="text-xs text-muted-foreground">Initial route</div>
                      <div className="mt-1 font-medium">
                        {entry.initial_route.intent}
                      </div>
                    </div>
                    <div className="rounded-lg border border-border bg-background p-3">
                      <div className="text-xs text-muted-foreground">Fallback</div>
                      <div className="mt-1 font-medium">
                        {entry.fallback.intent || "not used"}
                      </div>
                    </div>
                    <div className="rounded-lg border border-border bg-background p-3">
                      <div className="text-xs text-muted-foreground">Final route</div>
                      <div className="mt-1 font-medium">{entry.final_route.intent}</div>
                    </div>
                  </div>
                </div>
              </CollapsibleTrigger>
              <CollapsibleContent className="border-t border-border p-4 pt-0">
                <div className="grid gap-2 pt-4 md:grid-cols-3">
                  <div className="rounded-lg border border-border bg-background p-3">
                    <div className="text-xs text-muted-foreground">
                      Initial route details
                    </div>
                    <div className="mt-1 font-medium">{entry.initial_route.intent}</div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {entry.initial_route.source} · {entry.initial_route.confidence}
                    </div>
                  </div>
                  <div className="rounded-lg border border-border bg-background p-3">
                    <div className="text-xs text-muted-foreground">
                      Fallback details
                    </div>
                    <div className="mt-1 font-medium">
                      {entry.fallback.intent || "not used"}
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {entry.fallback.reason || "none"}
                    </div>
                  </div>
                  <div className="rounded-lg border border-border bg-background p-3">
                    <div className="text-xs text-muted-foreground">
                      Final route details
                    </div>
                    <div className="mt-1 font-medium">{entry.final_route.intent}</div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {entry.final_route.source} · {entry.final_route.confidence}
                    </div>
                  </div>
                </div>
                <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
                  <div>conversation: {entry.conversation_id || "n/a"}</div>
                  <div>session: {entry.session_id || "n/a"}</div>
                  <div>message: {entry.message_id || "n/a"}</div>
                  <div>channel: {entry.channel || "n/a"}</div>
                </div>
              </CollapsibleContent>
            </Collapsible>
          ))}
          {entries.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No route diagnostics have been stored yet.
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
