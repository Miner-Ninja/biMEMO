import { useTranslation } from "react-i18next";
import { TypeBadge } from "@/components/admin/memory-debug/shared";
import type { TranslationKey } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The one way an assertion is displayed in the debug tool (audit: the
 * search list, the timeline, and the links chain each showed a
 * different field set for the same object). Renders whatever subset
 * the data source provides.
 */
export interface AssertionCardData {
  id: string;
  text: string;
  relation?: string | undefined;
  trustLevel?: string | undefined;
  validFrom?: string | null | undefined;
  invalidFrom?: string | null | undefined;
  createdAt?: string | null | undefined;
  linkedRecordCount?: number;
}

const TRUST_KEYS: Record<string, TranslationKey> = {
  high: "memory.trustHigh",
  medium: "memory.trustMedium",
  low: "memory.trustLow",
};

const TRUST_COLORS: Record<string, string> = {
  high: "text-green-600 dark:text-green-400",
  medium: "text-amber-600 dark:text-amber-400",
  low: "text-muted-foreground",
};

export function AssertionCard({
  data,
  className,
}: {
  data: AssertionCardData;
  className?: string;
}) {
  const { t } = useTranslation();
  const invalidated = Boolean(data.invalidFrom);
  const shownDate = (data.validFrom ?? data.createdAt ?? "").slice(0, 10);
  const trustKey = data.trustLevel ? TRUST_KEYS[data.trustLevel] : undefined;

  return (
    <div className={cn("space-y-1", invalidated && "opacity-60", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <TypeBadge type="assertion" />
        {data.relation ? (
          <span className="text-xs text-muted-foreground">({data.relation})</span>
        ) : null}
        {trustKey ? (
          <span className={cn("text-xs", TRUST_COLORS[data.trustLevel ?? ""])}>
            {t(trustKey)}
          </span>
        ) : null}
        {shownDate ? (
          <span className="ml-auto text-xs text-muted-foreground">{shownDate}</span>
        ) : null}
      </div>
      <div className="text-sm whitespace-pre-wrap">{data.text}</div>
      <div className="flex flex-wrap gap-3 text-xs">
        {invalidated ? (
          <span className="text-red-500">
            {t("memoryDebug.invalidatedOn", {
              date: (data.invalidFrom ?? "").slice(0, 10),
            })}
          </span>
        ) : null}
        {typeof data.linkedRecordCount === "number" ? (
          <span className="text-muted-foreground">
            {t("memoryDebug.linkedRecords", { count: data.linkedRecordCount })}
          </span>
        ) : null}
      </div>
    </div>
  );
}
