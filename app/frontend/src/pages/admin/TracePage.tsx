import { useState } from "react";

import { AdminLayout } from "@/components/layout/AdminLayout";
import { Button } from "@/components/ui/button";
import { ApiError, getTurnTrace, type TurnTrace } from "@/lib/bimemo-api";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="mb-2 text-sm font-semibold text-foreground">{title}</h3>
      {children}
    </div>
  );
}

function Json({ value }: { value: unknown }) {
  return (
    <pre className="overflow-x-auto rounded bg-muted/50 p-3 font-mono text-xs text-foreground">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

/** Admin turn-trace viewer: full per-turn observability for one agent run. */
export function TracePage() {
  const [runId, setRunId] = useState("");
  const [trace, setTrace] = useState<TurnTrace | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async () => {
    const id = runId.trim();
    if (!id) return;
    setLoading(true);
    setError("");
    setTrace(null);
    try {
      setTrace(await getTurnTrace(id));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Failed to load trace");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AdminLayout title="Turn trace">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={runId}
            onChange={(event) => setRunId(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && void load()}
            placeholder="agent run id"
            className="min-w-[22rem] flex-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm"
          />
          <Button onClick={() => void load()} disabled={loading || !runId.trim()}>
            {loading ? "Loading…" : "Load trace"}
          </Button>
        </div>

        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}

        {trace && (
          <div className="space-y-4">
            <Section title="Run">
              <Json value={trace.run} />
            </Section>
            <Section
              title={`User selections & model signals (${Object.keys(trace.signals).length})`}
            >
              <Json value={trace.signals} />
            </Section>
            <Section title={`Tool calls (${trace.tool_calls.length})`}>
              <Json value={trace.tool_calls} />
            </Section>
            <Section
              title={`Confirmations & decisions (${trace.confirmations.length})`}
            >
              <Json value={trace.confirmations} />
            </Section>
            <Section title={`Event timeline (${trace.events.length})`}>
              <Json value={trace.events} />
            </Section>
          </div>
        )}
      </div>
    </AdminLayout>
  );
}
