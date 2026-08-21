import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { AutoRefreshBadge, NamedBadge } from "@/features/memory/components/shared";
import { EXTRACTOR_DECISION_COLORS } from "@/features/memory/components/constants";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { apiRequest } from "@/services/bimemo-api";

interface ExtractorTraceEntry {
  timestamp: string;
  decision: string;
  [key: string]: unknown;
}

function formatLogTimestamp(value: string): string {
  return value.replace("T", " ").replace(/\.\d+Z?$/, "");
}

/** Fields other than timestamp/decision — the decision-specific detail. */
function detailEntries(entry: ExtractorTraceEntry): Array<[string, string]> {
  return Object.entries(entry)
    .filter(([key]) => key !== "timestamp" && key !== "decision")
    .map(([key, value]) => [
      key,
      typeof value === "string" ? value : JSON.stringify(value),
    ]);
}

export function ExtractorTracePanel({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<ExtractorTraceEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(false);

  const fetchEntries = useCallback(async (): Promise<void> => {
    setLoading(true);
    const params = new URLSearchParams({ limit: "100" });
    if (filter) params.set("decision", filter);
    try {
      const data = await apiRequest<{ entries: ExtractorTraceEntry[] }>(
        `/admin/memory/extractor-trace?${params}`
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
      <p className="font-prose text-sm text-muted-foreground">
        {t("memoryDebug.extractorTraceIntro")}
      </p>
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
        {entries.map((entry, index) => {
          const details = detailEntries(entry);
          return (
            <Card key={`${entry.timestamp}-${index}`}>
              <Collapsible defaultOpen={index === 0}>
                <CollapsibleTrigger className="w-full">
                  <CardHeader className="pb-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <NamedBadge
                        value={entry.decision}
                        colors={EXTRACTOR_DECISION_COLORS}
                      />
                      <span className="text-xs text-muted-foreground">
                        {formatLogTimestamp(entry.timestamp)}
                      </span>
                    </div>
                  </CardHeader>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <CardContent className="space-y-2">
                    {details.length > 0 ? (
                      details.map(([key, value]) => (
                        <div key={key} className="text-xs">
                          <span className="text-muted-foreground">{key}: </span>
                          <span className="whitespace-pre-wrap">{value}</span>
                        </div>
                      ))
                    ) : (
                      <div className="font-prose text-xs text-muted-foreground">
                        {t("memoryDebug.extractorNoDetail")}
                      </div>
                    )}
                  </CardContent>
                </CollapsibleContent>
              </Collapsible>
            </Card>
          );
        })}
        {entries.length === 0 && !loading ? (
          <Card>
            <CardContent className="font-prose p-4 text-sm text-muted-foreground">
              {t("memoryDebug.noExtractorTrace")}
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
