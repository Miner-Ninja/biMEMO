import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { RecordsLogEntry } from "@/features/memory/components/types";
import { RecordsLogBadge, AutoRefreshBadge } from "@/features/memory/components/shared";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { apiRequest } from "@/services/bimemo-api";

function formatLogTimestamp(value: string): string {
  return value.replace("T", " ").replace(/\.\d+Z$/, "Z");
}

function shortId(value: string | undefined): string {
  if (!value) return "";
  return value.length > 8 ? value.slice(0, 8) : value;
}

export function RecordsLogPanel({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<RecordsLogEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(false);

  const fetchEntries = useCallback(async (): Promise<void> => {
    setLoading(true);
    const params = new URLSearchParams({ limit: "100" });
    if (filter) params.set("decision", filter);
    try {
      const data = await apiRequest<{ entries: RecordsLogEntry[] }>(
        `/admin/memory/dialog-recorder-trace?${params}`
      );
      setEntries(data.entries || []);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    fetchEntries();
    const intervalId = window.setInterval(fetchEntries, 5000);
    return () => window.clearInterval(intervalId);
  }, [fetchEntries, refreshToken]);

  const decisions = [...new Set(entries.map((entry) => entry.decision))].sort();

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant={filter === "" ? "default" : "outline"}
          onClick={() => setFilter("")}
        >
          {t("memoryDebug.all")}
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
        <span className="ml-auto">
          <AutoRefreshBadge seconds={5} />
        </span>
      </div>

      <div className="space-y-3">
        {entries.map((entry, index) => (
          <Card key={`${entry.timestamp}-${index}`}>
            <Collapsible defaultOpen={index === 0}>
              <CollapsibleTrigger className="w-full">
                <CardHeader className="space-y-3 pb-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <RecordsLogBadge decision={entry.decision} />
                    <span className="text-xs text-muted-foreground">
                      {formatLogTimestamp(entry.timestamp)}
                    </span>
                    {entry.record_id ? (
                      <span className="text-xs text-muted-foreground">
                        {t("memoryDebug.recordShortId", {
                          id: shortId(entry.record_id),
                        })}
                      </span>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                    <span>
                      {t("memoryDebug.messagesCountLabel", {
                        value: entry.message_count,
                      })}
                    </span>
                    <span>
                      {t("memoryDebug.sourceIdsLabel", {
                        value: entry.source_message_ids?.length ?? 0,
                      })}
                    </span>
                  </div>
                  {entry.summary ? (
                    <div className="line-clamp-3 text-sm leading-6 whitespace-pre-wrap text-left">
                      {entry.summary}
                    </div>
                  ) : null}
                </CardHeader>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <CardContent className="space-y-3">
                  {entry.model ? (
                    <div className="text-xs text-muted-foreground">
                      {t("memoryDebug.modelLabel", { value: entry.model })}
                    </div>
                  ) : null}
                  <div className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
                    <div>
                      {t("memoryDebug.conversationLabel", {
                        value: entry.conversation_id || t("memoryDebug.notAvailable"),
                      })}
                    </div>
                    <div>
                      {t("memoryDebug.sessionLabel", {
                        value: entry.session_id || t("memoryDebug.notAvailable"),
                      })}
                    </div>
                  </div>
                  {entry.key_facts?.length ? (
                    <div className="text-xs text-muted-foreground">
                      {t("memoryDebug.keyFactsLabel", {
                        value: entry.key_facts.join(" | "),
                      })}
                    </div>
                  ) : null}
                  {entry.action_items?.length ? (
                    <div className="text-xs text-muted-foreground">
                      {t("memoryDebug.actionItemsLabel", {
                        value: entry.action_items.join(" | "),
                      })}
                    </div>
                  ) : null}
                  {entry.error ? (
                    <div className="text-xs text-red-400">{entry.error}</div>
                  ) : null}
                  {entry.detail ? (
                    <div className="text-xs text-muted-foreground">{entry.detail}</div>
                  ) : null}
                  <details className="text-xs">
                    <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                      {t("memoryDebug.inputMessages")}
                    </summary>
                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-foreground">
                      {entry.messages.join("\n---\n")}
                    </pre>
                  </details>
                  {entry.prompt?.length ? (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                        {t("memoryDebug.promptLabel")}
                      </summary>
                      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-foreground">
                        {JSON.stringify(entry.prompt, null, 2)}
                      </pre>
                    </details>
                  ) : null}
                  {entry.raw_output ? (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                        {t("memoryDebug.rawOutput")}
                      </summary>
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
        {entries.length === 0 && !loading ? (
          <Card>
            <CardContent className="font-prose p-4 text-sm text-muted-foreground">
              {t("memoryDebug.noRecordLogEntries")}
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
