import { useEffect, useState } from "react";

import { PanelSkeleton } from "@/components/ui/skeletons";
import type { ContextData } from "@/components/admin/memory-debug/types";
import {
  ContentDisplay,
  normalizeStringArray,
  RecordRelationBadge,
  TypeBadge,
} from "@/components/admin/memory-debug/shared";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiRequest } from "@/lib/bimemo-api";

export function MemoryLinksPanel({ refreshToken }: { refreshToken: number }) {
  const [data, setData] = useState<ContextData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiRequest<ContextData>("/admin/memory/context")
      .then(setData)
      .catch((requestError) => setError(String(requestError)));
  }, [refreshToken]);

  if (error)
    return (
      <div className="p-4 text-sm text-red-400">
        Memory links could not be loaded: {error}
      </div>
    );
  if (!data) return <PanelSkeleton />;

  const messageEntries = data.tiers?.recent_messages?.entries || [];
  const recordEntries = data.tiers?.records?.entries || [];
  const assertionEntries = data.tiers?.assertions?.entries || [];
  const messagesById = new Map(
    messageEntries.map((entry) => [String(entry.id || ""), entry])
  );
  const recordsById = new Map(
    recordEntries.map((entry) => [String(entry.id || ""), entry])
  );

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Assertion to record to message chain</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {assertionEntries.map((summaryEntry) => {
            const recordRelations = normalizeStringArray(
              summaryEntry.source_record_ids
            ).map((recordId) => ({
              record_id: recordId,
              relation: String(summaryEntry.relation || "supports"),
            }));
            const linkedRecords = recordRelations
              .map((link) => {
                const recordId = String(link.record_id || "");
                const recordEntry = recordsById.get(recordId);
                if (!recordEntry) return null;
                return {
                  relation: String(link.relation || "supports"),
                  entry: recordEntry,
                };
              })
              .filter(Boolean) as Array<{
              relation: string;
              entry: Record<string, unknown>;
            }>;

            return (
              <Collapsible
                key={String(summaryEntry.id)}
                className="rounded-xl border border-border bg-muted/10"
              >
                <CollapsibleTrigger className="w-full p-4 text-left">
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <TypeBadge type="assertion" />
                      {summaryEntry.invalid_from ? (
                        <span className="text-xs text-red-400">invalidated</span>
                      ) : null}
                      <span className="ml-auto text-xs text-muted-foreground">
                        {String(summaryEntry.created_at || "")}
                      </span>
                    </div>
                    <div className="line-clamp-3 text-sm whitespace-pre-wrap text-left">
                      {String(
                        summaryEntry.assertion_text || summaryEntry.content || ""
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {linkedRecords.length} linked record
                      {linkedRecords.length === 1 ? "" : "s"}
                    </div>
                  </div>
                </CollapsibleTrigger>
                <CollapsibleContent className="border-t border-border p-4 pt-4">
                  <div className="space-y-3 border-l border-border pl-4">
                    {linkedRecords.map(({ relation, entry: recordEntry }) => {
                      const sourceMessageIds = normalizeStringArray(
                        recordEntry.source_message_ids
                      );
                      const linkedMessages = sourceMessageIds
                        .map((messageId) => messagesById.get(messageId))
                        .filter(Boolean) as Array<Record<string, unknown>>;
                      return (
                        <div key={String(recordEntry.id)} className="space-y-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <TypeBadge type="record" />
                            <RecordRelationBadge relation={relation} />
                            <span className="text-xs text-muted-foreground">
                              {String(recordEntry.created_at || "")}
                            </span>
                          </div>
                          <div className="text-sm whitespace-pre-wrap">
                            {String(recordEntry.summary || recordEntry.content || "")}
                          </div>
                          {linkedMessages.length > 0 ? (
                            <div className="space-y-2 border-l border-border/70 pl-4">
                              {linkedMessages.map((messageEntry) => (
                                <div
                                  key={String(messageEntry.id)}
                                  className="space-y-1"
                                >
                                  <div className="flex flex-wrap items-center gap-2">
                                    <TypeBadge
                                      type={String(messageEntry.role || "message")}
                                    />
                                    <span className="text-xs text-muted-foreground">
                                      {String(messageEntry.created_at || "")}
                                    </span>
                                  </div>
                                  <div className="text-sm text-foreground/90 whitespace-pre-wrap">
                                    <ContentDisplay
                                      content={messageEntry.content || ""}
                                    />
                                  </div>
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                    {linkedRecords.length === 0 ? (
                      <div className="text-sm text-muted-foreground">
                        No linked records are currently available for this summary.
                      </div>
                    ) : null}
                  </div>
                </CollapsibleContent>
              </Collapsible>
            );
          })}
          {assertionEntries.length === 0 ? (
            <div className="text-sm text-muted-foreground">
              No assertion chains are available yet.
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
