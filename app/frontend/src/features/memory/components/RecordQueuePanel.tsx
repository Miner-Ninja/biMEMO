import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { PanelSkeleton } from "@/components/ui/skeletons";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { apiRequest } from "@/services/bimemo-api";

interface QueueItem {
  queue_id: string;
  conversation_id: string;
  session_id: string;
  source_message_ids: string[];
  injection_flagged: boolean;
  attempts: number;
  created_at: string;
}

function formatTimestamp(value: string): string {
  return value.replace("T", " ").replace(/\.\d+Z?$/, "");
}

export function RecordQueuePanel({ refreshToken }: { refreshToken: number }) {
  const { t } = useTranslation();
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiRequest<{ items: QueueItem[] }>("/admin/memory/record-queue?limit=100")
      .then((data) => {
        setItems(data.items || []);
        setError("");
      })
      .catch((requestError) => setError(String(requestError)));
  }, [refreshToken]);

  if (error)
    return (
      <div className="p-4 text-sm text-red-400">
        {t("memoryDebug.queueLoadError", { error })}
      </div>
    );
  if (!items) return <PanelSkeleton />;

  return (
    <div className="space-y-4">
      <p className="font-prose text-sm text-muted-foreground">
        {t("memoryDebug.recordQueueIntro")}
      </p>
      {items.length === 0 ? (
        <Card>
          <CardContent className="font-prose p-4 text-sm text-muted-foreground">
            {t("memoryDebug.recordQueueEmpty")}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {items.map((item) => (
            <Card key={item.queue_id}>
              <CardContent className="space-y-2 p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-muted-foreground">
                    {formatTimestamp(item.created_at)}
                  </span>
                  {item.injection_flagged ? (
                    <Badge variant="danger">
                      {t("memoryDebug.queueInjectionFlagged")}
                    </Badge>
                  ) : null}
                  {item.attempts > 0 ? (
                    <Badge variant="warning">
                      {t("memoryDebug.queueAttempts", { count: item.attempts })}
                    </Badge>
                  ) : null}
                </div>
                <div className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
                  <div>
                    {t("memoryDebug.conversationLabel", {
                      value: item.conversation_id || t("memoryDebug.notAvailable"),
                    })}
                  </div>
                  <div>
                    {t("memoryDebug.sessionLabel", {
                      value: item.session_id || t("memoryDebug.notAvailable"),
                    })}
                  </div>
                </div>
                <div className="text-xs text-muted-foreground">
                  {t("memoryDebug.sourceIdsLabel", {
                    value: item.source_message_ids.length,
                  })}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
