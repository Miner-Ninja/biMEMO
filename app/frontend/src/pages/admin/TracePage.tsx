import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { AdminLayout } from "@/components/layout/AdminLayout";
import { Button } from "@/components/ui/button";
import { getRecentTraces, getTurnTrace, type TurnTrace } from "@/lib/bimemo-api";
import { QUERY_KEYS, errorToDisplayText } from "@/hooks/use-server-state";
import { cn } from "@/lib/utils";

function statusClass(status: string): string {
  if (status === "completed") return "bg-success/15 text-success";
  if (status === "failed") return "bg-destructive/15 text-destructive";
  if (status === "waiting") return "bg-warning/15 text-warning";
  return "bg-muted text-muted-foreground";
}

function shortTime(iso: string): string {
  return iso ? iso.replace("T", " ").slice(0, 19) : "";
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
        {title}
      </h3>
      {children}
    </div>
  );
}

function Json({ value }: { value: unknown }) {
  return (
    <pre className="max-h-80 overflow-auto rounded bg-muted/50 p-3 font-mono text-xs text-foreground">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function TraceDetail({ trace }: { trace: TurnTrace }) {
  const { messages } = trace;
  return (
    <div className="space-y-3">
      {messages.user && (
        <Section title="User request">
          <p className="whitespace-pre-wrap break-words text-sm text-foreground">
            {messages.user.content || "(empty)"}
          </p>
        </Section>
      )}
      {messages.assistant && (
        <Section title="Assistant answer">
          <p className="whitespace-pre-wrap break-words text-sm text-foreground">
            {messages.assistant.content || "(empty)"}
          </p>
        </Section>
      )}
      <Section
        title={`Selections & model signals (${Object.keys(trace.signals).length})`}
      >
        <Json value={trace.signals} />
      </Section>
      <Section title={`Tool calls (${trace.tool_calls.length})`}>
        <Json value={trace.tool_calls} />
      </Section>
      <Section title={`Confirmations & decisions (${trace.confirmations.length})`}>
        <Json value={trace.confirmations} />
      </Section>
      <Section title={`Event timeline (${trace.events.length})`}>
        <Json value={trace.events} />
      </Section>
      <Section title="Run">
        <Json value={trace.run} />
      </Section>
    </div>
  );
}

/** Admin turn-trace: pick a recent run to see its full per-turn trace. */
export function TracePage() {
  const { t } = useTranslation();
  const [selectedId, setSelectedId] = useState("");

  const {
    data: runs = [],
    isInitialLoading: loadingList,
    error: listError,
    refetch,
  } = useQuery({
    queryKey: QUERY_KEYS.traces,
    queryFn: () => getRecentTraces(80),
  });

  const {
    data: trace,
    isInitialLoading: loadingTrace,
    error: traceError,
  } = useQuery({
    queryKey: QUERY_KEYS.trace(selectedId),
    queryFn: () => getTurnTrace(selectedId),
    enabled: selectedId !== "",
  });

  const error =
    (listError ? errorToDisplayText(listError, t) : "") ||
    (traceError ? errorToDisplayText(traceError, t) : "");

  const openRun = (id: string) => {
    setSelectedId(id);
  };

  return (
    <AdminLayout title="Turn trace">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Recent turns, newest first. Click one to see its full trace.
        </p>
        <Button variant="outline" size="sm" onClick={() => void refetch()}>
          Refresh
        </Button>
      </div>

      {error && (
        <div className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(20rem,26rem)_minmax(0,1fr)]">
        <div className="max-h-[75vh] min-w-0 space-y-1 overflow-auto rounded-lg border border-border p-1">
          {loadingList && <p className="p-3 text-sm text-muted-foreground">Loading…</p>}
          {!loadingList && runs.length === 0 && (
            <p className="p-3 text-sm text-muted-foreground">No runs yet.</p>
          )}
          {runs.map((run) => (
            <button
              key={run.id}
              type="button"
              onClick={() => void openRun(run.id)}
              className={cn(
                "w-full rounded-md px-3 py-2 text-left transition-colors hover:bg-muted",
                selectedId === run.id && "bg-muted"
              )}
            >
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    "rounded px-1.5 py-0.5 text-[10px] font-medium",
                    statusClass(run.status)
                  )}
                >
                  {run.status}
                </span>
                <span className="truncate font-mono text-[11px] text-muted-foreground">
                  {shortTime(run.created_at)} · {run.channel}
                </span>
              </div>
              <div className="mt-1 line-clamp-2 text-sm text-foreground">
                {run.user_message || (
                  <span className="text-muted-foreground">(no request text)</span>
                )}
              </div>
            </button>
          ))}
        </div>

        <div className="max-h-[75vh] min-w-0 overflow-auto pr-1">
          {loadingTrace && (
            <p className="p-3 text-sm text-muted-foreground">Loading trace…</p>
          )}
          {!loadingTrace && trace && <TraceDetail trace={trace} />}
          {!loadingTrace && !trace && (
            <p className="p-3 text-sm text-muted-foreground">
              Select a run on the left.
            </p>
          )}
        </div>
      </div>
    </AdminLayout>
  );
}
