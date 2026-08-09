import { useCallback, useEffect, useState } from "react";

import { SORT_OPTIONS } from "@/components/admin/memory-debug/constants";
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

function reorderMessagePairs(entries: TimelineEntry[]): TimelineEntry[] {
  const result: TimelineEntry[] = [];
  let index = 0;
  while (index < entries.length) {
    const entry = entries[index];
    if (
      entry.type === "message" &&
      entry.meta?.role === "assistant" &&
      index + 1 < entries.length &&
      entries[index + 1].type === "message" &&
      entries[index + 1].meta?.role === "user"
    ) {
      result.push(entries[index + 1], entry);
      index += 2;
    } else {
      result.push(entry);
      index += 1;
    }
  }
  return result;
}

export function MemoryTimelinePanel({ refreshToken }: { refreshToken: number }) {
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
        title="Timeline"
        description="Read memory as a time-ordered stream of messages, records, and summaries."
        actions={
          <>
            {["", "message", "record", "assertion"].map((type) => (
              <Button
                key={type || "all"}
                size="sm"
                variant={filter === type ? "default" : "outline"}
                onClick={() => setFilter(type)}
              >
                {type || "All"}
              </Button>
            ))}
            {SORT_OPTIONS.map((option) => (
              <Button
                key={option.value}
                size="sm"
                variant={sort === option.value ? "default" : "outline"}
                onClick={() => setSort(option.value)}
              >
                {option.label}
              </Button>
            ))}
          </>
        }
      />

      <div className="space-y-3">
        {entries.map((entry) => {
          const role = entry.type === "message" ? entry.meta?.role || "message" : "";
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
            <CardContent className="p-4 text-sm text-muted-foreground">
              No entries match the current timeline filter.
            </CardContent>
          </Card>
        ) : null}
        {entries.length > 0 ? (
          <div className="text-xs text-muted-foreground">
            Showing {entries.length} of {totalCount}
          </div>
        ) : null}
        {hasMore ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchEntries(false)}
            disabled={loading}
          >
            {loading ? "Loading..." : "Load more"}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
