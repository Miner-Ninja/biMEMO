import { useCallback, useEffect, useState } from "react"

import type { SummariesLogEntry } from "@/components/admin/memory-debug/types"
import { SummariesLogBadge, SummaryScopeBadge } from "@/components/admin/memory-debug/shared"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { apiRequest } from "@/lib/bimemo-api"

function formatLogTimestamp(value: string): string {
  return value.replace("T", " ").replace(/\.\d+Z$/, "Z")
}

function shortId(value: string | undefined): string {
  if (!value) return ""
  return value.length > 8 ? value.slice(0, 8) : value
}

export function SummariesLogPanel({ refreshToken }: { refreshToken: number }) {
  const [entries, setEntries] = useState<SummariesLogEntry[]>([])
  const [filter, setFilter] = useState("")
  const [loading, setLoading] = useState(false)

  const fetchEntries = useCallback(async (): Promise<void> => {
    setLoading(true)
    const params = new URLSearchParams({ limit: "100" })
    if (filter) params.set("decision", filter)
    try {
      const data = await apiRequest<{ entries: SummariesLogEntry[] }>(`/admin/memory/distiller-trace?${params}`)
      setEntries(data.entries || [])
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    fetchEntries()
    const intervalId = window.setInterval(fetchEntries, 5000)
    return () => window.clearInterval(intervalId)
  }, [fetchEntries, refreshToken])

  const decisions = [...new Set(entries.map((entry) => entry.decision))].sort()

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant={filter === "" ? "default" : "outline"} onClick={() => setFilter("")}>
          All
        </Button>
        {decisions.map((decision) => (
          <Button
            key={decision}
            size="sm"
            variant={filter === decision ? "default" : "outline"}
            onClick={() => setFilter(decision)}
          >
            {decision.replace(/_/g, " ")}
          </Button>
        ))}
        <span className="ml-auto text-xs text-muted-foreground">
          Refreshes every 5 seconds{loading ? " · loading..." : ""}
        </span>
      </div>

      <div className="space-y-3">
        {entries.map((entry, index) => (
          <Card key={`${entry.timestamp}-${index}`}>
            <Collapsible>
              <CollapsibleTrigger className="w-full">
                <CardHeader className="space-y-3 pb-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <SummariesLogBadge decision={entry.decision} />
                    <span className="text-xs text-muted-foreground">{formatLogTimestamp(entry.timestamp)}</span>
                    {entry.summary_id ? <span className="text-xs text-muted-foreground">summary {shortId(entry.summary_id)}</span> : null}
                  </div>
                  <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                    <span>records {entry.record_count}</span>
                    <span>source IDs {entry.source_record_ids?.length ?? 0}</span>
                  </div>
                  {entry.main_point ? <div className="line-clamp-3 text-left text-sm leading-6 whitespace-pre-wrap">{entry.main_point}</div> : null}
                </CardHeader>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <CardContent className="space-y-3">
                  {entry.model ? <div className="text-xs text-muted-foreground">model: {entry.model}</div> : null}
                  {entry.trust_level ? <div className="text-xs text-muted-foreground">trust: {entry.trust_level}</div> : null}
                  {entry.scope ? <div className="flex items-center gap-2 text-xs text-muted-foreground"><span>scope:</span><SummaryScopeBadge scope={entry.scope} /></div> : null}
                  {entry.based_on?.length ? <div className="text-xs text-muted-foreground">based on: {entry.based_on.join(" | ")}</div> : null}
                  {entry.record_relations?.length ? <div className="text-xs text-muted-foreground">relations: {entry.record_relations.join(" | ")}</div> : null}
                  {entry.error ? <div className="text-xs text-red-400">{entry.error}</div> : null}
                  {entry.detail ? <div className="text-xs text-muted-foreground">{entry.detail}</div> : null}
                  <details className="text-xs">
                    <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Input records</summary>
                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-foreground">
                      {JSON.stringify(entry.records, null, 2)}
                    </pre>
                  </details>
                  {entry.prompt?.length ? (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Prompt</summary>
                      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-foreground">
                        {JSON.stringify(entry.prompt, null, 2)}
                      </pre>
                    </details>
                  ) : null}
                  {entry.raw_output ? (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Raw output</summary>
                      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-foreground">
                        {entry.raw_output}
                      </pre>
                    </details>
                  ) : null}
                </CardContent>
              </CollapsibleContent>
            </Collapsible>
          </Card>
        ))}
        {entries.length === 0 && !loading ? <Card><CardContent className="p-4 text-sm text-muted-foreground">No summary log entries match the current filter.</CardContent></Card> : null}
      </div>
    </div>
  )
}
