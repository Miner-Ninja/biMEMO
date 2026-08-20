import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { TranslationKey } from "@/i18n";
import { SORT_OPTIONS } from "@/components/admin/memory-debug/constants";
import { AssertionCard } from "@/components/admin/memory-debug/AssertionCard";
import type {
  TimelineData,
  TimelineEntry,
} from "@/components/admin/memory-debug/types";
import {
  ContentDisplay,
  MetaBadges,
  SectionIntro,
  TypeBadge,
} from "@/components/admin/memory-debug/shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { apiRequest } from "@/lib/bimemo-api";

const TYPE_FILTERS: ReadonlyArray<{ value: string; labelKey: TranslationKey }> = [
  { value: "", labelKey: "memoryDebug.all" },
  { value: "message", labelKey: "memoryDebug.typeMessage" },
  { value: "record", labelKey: "memoryDebug.typeRecord" },
  { value: "assertion", labelKey: "memoryDebug.typeAssertion" },
];

function reorderMessagePairs(entries: TimelineEntry[]): TimelineEntry[] {
  const result: TimelineEntry[] = [];
  for (let index = 0; index < entries.length; index++) {
    const entry = entries[index];
    if (entry === undefined) continue;
    const nextEntry = entries[index + 1];
    if (
      entry.type === "message" &&
      entry.meta?.["role"] === "assistant" &&
      nextEntry !== undefined &&
      nextEntry.type === "message" &&
      nextEntry.meta?.["role"] === "user"
    ) {
      result.push(nextEntry, entry);
      index++;
    }
  }
  return result;
}

export function MemoryTimelinePanel({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<TimelineEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<"desc" | "asc">("desc");
  const [loading, setLoading] = useState(false);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [totalCount, setTotalCount] = useState(0);

  const fetchEntries = useCallback(
    async (reset = false): Promise<void> => {
      setLoading(true);
      const nextOffset = reset ? 0 : offset;
      const params = new URLSearchParams({
        limit: "50",
        offset: String(nextOffset),
        sort,
      });
      if (filter) params.set("type", filter);
      try {
        const data = await apiRequest<TimelineData>(`/admin/memory/timeline?${params}`);
        const nextEntries =
          sort === "desc"
            ? reorderMessagePairs(data.entries || [])
            : data.entries || [];
        setTotalCount(data.total_count || 0);
        if (reset) {
          setEntries(nextEntries);
          setOffset(50);
        } else {
          setEntries((previous) => reorderMessagePairs([...previous, ...nextEntries]));
          setOffset((previous) => previous + 50);
        }
        setHasMore(nextOffset + nextEntries.length < (data.total_count || 0));
      } finally {
        setLoading(false);
      }
    },
    [filter, offset, sort]
  );

  useEffect(() => {
    fetchEntries(true);
  }, [fetchEntries, refreshToken]);

  return (
    <div className="space-y-6">
      <SectionIntro
        title={t("memoryDebug.viewTimeline")}
        description={t("memoryDebug.timelineIntro")}
        actions={
          <>
            {TYPE_FILTERS.map(({ value, labelKey }) => (
              <Button
                key={value || "all"}
                size="sm"
                variant={filter === value ? "default" : "outline"}
                onClick={() => setFilter(value)}
              >
                {t(labelKey)}
              </Button>
            ))}
            {SORT_OPTIONS.map((option) => (
              <Button
                key={option.value}
                size="sm"
                variant={sort === option.value ? "default" : "outline"}
                onClick={() => setSort(option.value)}
              >
                {t(option.labelKey)}
              </Button>
            ))}
          </>
        }
      />

      <div className="space-y-3">
        {entries.map((entry) => {
          if (entry.type === "assertion") {
            const meta = entry.meta ?? {};
            return (
              <Card key={entry.id}>
                <CardContent className="p-4">
                  <AssertionCard
                    data={{
                      id: entry.id,
                      text: typeof entry.content === "string" ? entry.content : "",
                      relation:
                        typeof meta["relation"] === "string"
                          ? meta["relation"]
                          : undefined,
                      trustLevel:
                        typeof meta["trust_level"] === "string"
                          ? meta["trust_level"]
                          : undefined,
                      invalidFrom:
                        typeof meta["invalid_from"] === "string"
                          ? meta["invalid_from"]
                          : null,
                      createdAt: entry.created_at,
                    }}
                  />
                </CardContent>
              </Card>
            );
          }
          const role =
            entry.type === "message" ? entry.meta?.["role"] || "message" : "";
          const badgeType = role === "user" || role === "assistant" ? role : entry.type;
          return (
            <Card key={entry.id}>
              <CardContent className="space-y-2 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <TypeBadge type={badgeType} />
                  <span className="text-xs text-muted-foreground">
                    {entry.created_at}
                  </span>
                </div>
                <div className="text-sm whitespace-pre-wrap">
                  <ContentDisplay content={entry.content} />
                </div>
                <MetaBadges meta={entry.meta} />
              </CardContent>
            </Card>
          );
        })}
        {entries.length === 0 && !loading ? (
          <Card>
            <CardContent className="font-prose p-4 text-sm text-muted-foreground">
              {t("memoryDebug.noTimelineEntries")}
            </CardContent>
          </Card>
        ) : null}
        {entries.length > 0 ? (
          <div className="text-xs text-muted-foreground">
            {t("memoryDebug.showingOf", { shown: entries.length, total: totalCount })}
          </div>
        ) : null}
        {hasMore ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchEntries(false)}
            disabled={loading}
          >
            {loading ? t("common.loading") : t("memoryDebug.loadMore")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
