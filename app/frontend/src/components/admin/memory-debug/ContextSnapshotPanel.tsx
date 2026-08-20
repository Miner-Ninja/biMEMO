import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { PanelSkeleton } from "@/components/ui/skeletons";
import type { ContextData } from "@/components/admin/memory-debug/types";
import {
  ContentDisplay,
  MetaBadges,
  SummaryScopeBadge,
  TypeBadge,
} from "@/components/admin/memory-debug/shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiRequest } from "@/lib/bimemo-api";

function sortEntries(
  entries: Array<Record<string, unknown>>,
  isMessages: boolean,
  order: "desc" | "asc"
) {
  const sorted = [...entries].sort((left, right) =>
    String(left["created_at"] || "").localeCompare(String(right["created_at"] || ""))
  );

  if (!isMessages) {
    return order === "desc" ? sorted.reverse() : sorted;
  }

  const grouped: Array<Array<Record<string, unknown>>> = [];
  let current: Array<Record<string, unknown>> = [];
  for (const entry of sorted) {
    if (entry["role"] === "user") {
      if (current.length > 0) grouped.push(current);
      current = [entry];
    } else {
      current.push(entry);
    }
  }
  if (current.length > 0) grouped.push(current);
  if (order === "desc") grouped.reverse();
  return grouped.flat();
}

export function ContextSnapshotPanel({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
  const [data, setData] = useState<ContextData | null>(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [sortOrder, setSortOrder] = useState<"desc" | "asc">("desc");

  useEffect(() => {
    apiRequest<ContextData>("/admin/memory/context")
      .then(setData)
      .catch((requestError) => setError(String(requestError)));
  }, [refreshToken]);

  if (error)
    return (
      <div className="p-4 text-sm text-red-400">
        {t("memoryDebug.contextLoadError", { error })}
      </div>
    );
  if (!data) return <PanelSkeleton />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant={sortOrder === "desc" ? "default" : "outline"}
          onClick={() => setSortOrder("desc")}
        >
          {t("memoryDebug.sortNewestFirst")}
        </Button>
        <Button
          size="sm"
          variant={sortOrder === "asc" ? "default" : "outline"}
          onClick={() => setSortOrder("asc")}
        >
          {t("memoryDebug.sortOldestFirst")}
        </Button>
      </div>

      <div className="space-y-4">
        {Object.entries(data.tiers || {}).map(([tierName, tier], tierIndex) => {
          const isMessages = tierName === "recent_messages";
          const entries = sortEntries(tier.entries, isMessages, sortOrder);
          const badgeType =
            tierName === "recent_messages"
              ? "message"
              : tierName === "records"
                ? "record"
                : "assertion";
          // The first tier opens by itself so the first screen shows data
          // (audit: collapsed bars carry zero information).
          const isExpanded = expanded[tierName] ?? tierIndex === 0;

          return (
            <Card key={tierName}>
              <CardHeader className="pb-3">
                <button
                  className="flex w-full items-center justify-between gap-4 text-left"
                  onClick={() =>
                    setExpanded((previous) => ({
                      ...previous,
                      [tierName]: !isExpanded,
                    }))
                  }
                >
                  <div className="flex items-center gap-3">
                    <TypeBadge type={badgeType} />
                    <CardTitle className="text-base">
                      {tierName.replace(/_/g, " ")}
                    </CardTitle>
                  </div>
                  <div className="text-sm text-muted-foreground">
                    {t("memoryDebug.entriesCount", {
                      count: tier.count ?? tier.entries.length,
                    })}
                  </div>
                </button>
              </CardHeader>
              {isExpanded ? (
                <CardContent className="space-y-2">
                  {entries.map((entry, index) => (
                    <div
                      key={`${tierName}-${index}`}
                      className="rounded-lg border border-border bg-muted/10 p-3"
                    >
                      <div className="mb-1 flex flex-wrap items-center gap-2">
                        {entry["role"] ? (
                          <TypeBadge type={String(entry["role"])} />
                        ) : null}
                        <span className="text-xs text-muted-foreground">
                          {String(entry["created_at"] || entry["id"] || "")}
                        </span>
                      </div>
                      <div className="text-sm text-foreground">
                        <ContentDisplay
                          content={
                            entry["content"] ||
                            entry["summary"] ||
                            entry["assertion_text"] ||
                            entry
                          }
                        />
                      </div>
                      {typeof entry["scope"] === "string" ? (
                        <div className="mt-2">
                          <SummaryScopeBadge scope={entry["scope"]} />
                        </div>
                      ) : null}
                      <MetaBadges meta={entry["meta"]} />
                    </div>
                  ))}
                  {entries.length === 0 ? (
                    <div className="font-prose text-sm text-muted-foreground">
                      {t("memoryDebug.noEntriesInTier")}
                    </div>
                  ) : null}
                </CardContent>
              ) : null}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
