import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { AuditEntry } from "@/features/memory/components/types";
import {
  AuditEventBadge,
  SectionIntro,
  formatTimestamp,
  AutoRefreshBadge,
} from "@/features/memory/components/shared";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { apiRequest } from "@/services/bimemo-api";

export function AuditDiagnosticsPanel({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [eventTypes, setEventTypes] = useState<string[]>([]);

  const fetchEntries = useCallback(async (): Promise<void> => {
    setLoading(true);
    const params = new URLSearchParams({ limit: "100", offset: "0" });
    if (filter) params.set("event_type", filter);
    try {
      const data = await apiRequest<{ entries: AuditEntry[] }>(
        `/admin/memory/audit?${params}`
      );
      const list = data.entries || [];
      setEntries(list);
      if (!filter) {
        setEventTypes([...new Set(list.map((entry) => entry.event_type))].sort());
      }
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    fetchEntries();
    const intervalId = window.setInterval(fetchEntries, 5000);
    return () => window.clearInterval(intervalId);
  }, [fetchEntries, refreshToken]);

  return (
    <div className="space-y-6">
      <SectionIntro
        title={t("memoryDebug.auditDiagnosticsTitle")}
        description={t("memoryDebug.auditDiagnosticsDescription")}
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          <Button
            variant={filter === "" ? "default" : "outline"}
            size="sm"
            onClick={() => setFilter("")}
          >
            {t("memoryDebug.all")}
          </Button>
          {eventTypes.map((eventType) => (
            <Button
              key={eventType}
              variant={filter === eventType ? "default" : "outline"}
              size="sm"
              onClick={() => setFilter(eventType)}
            >
              {eventType}
            </Button>
          ))}
        </div>
        <AutoRefreshBadge seconds={5} />
      </div>

      <div className="space-y-3">
        {entries.map((entry, index) => (
          <Card key={entry.id}>
            <Collapsible defaultOpen={index === 0}>
              <CollapsibleTrigger className="w-full">
                <CardHeader className="pb-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <AuditEventBadge eventType={entry.event_type} />
                    {entry.tool_name ? (
                      <span className="text-xs font-mono">{entry.tool_name}</span>
                    ) : null}
                    {entry.zone ? (
                      <span className="text-xs text-muted-foreground">
                        [{entry.zone}]
                      </span>
                    ) : null}
                    {entry.duration_ms != null ? (
                      <span className="text-xs text-muted-foreground">
                        {entry.duration_ms.toFixed(0)}ms
                      </span>
                    ) : null}
                    <span className="ml-auto text-xs text-muted-foreground">
                      {formatTimestamp(entry.timestamp)}
                    </span>
                  </div>
                </CardHeader>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <CardContent className="space-y-3">
                  {entry.parameters ? (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                        {t("memoryDebug.parameters")}
                      </summary>
                      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-foreground">
                        {entry.parameters}
                      </pre>
                    </details>
                  ) : null}
                  {entry.result ? (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                        {t("memoryDebug.result")}
                      </summary>
                      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-foreground">
                        {entry.result.slice(0, 1000)}
                        {entry.result.length > 1000 ? "..." : ""}
                      </pre>
                    </details>
                  ) : null}
                </CardContent>
              </CollapsibleContent>
            </Collapsible>
          </Card>
        ))}
        {entries.length === 0 && !loading ? (
          <Card>
            <CardContent className="font-prose p-4 text-sm text-muted-foreground">
              {t("memoryDebug.noAuditEntries")}
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
