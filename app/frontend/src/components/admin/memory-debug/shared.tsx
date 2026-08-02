import type { ReactNode } from "react";
import { FileText, ImageIcon, Mic } from "lucide-react";

import {
  AUDIT_EVENT_COLORS,
  NOTE_RELATION_COLORS,
  ROUTE_MISMATCH_COLORS,
  SUMMARY_SCOPE_COLORS,
  SUMMARY_STATUS_COLORS,
  SUMMARIES_LOG_COLORS,
  TYPE_COLORS,
  NOTES_LOG_COLORS,
} from "@/components/admin/memory-debug/constants";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** Format content that may be plain text or a multimodal array. */
export function formatContent(content: unknown): {
  text: string;
  hasImages: boolean;
  imageCount: number;
} {
  if (typeof content === "string") {
    return { text: content, hasImages: false, imageCount: 0 };
  }
  if (Array.isArray(content)) {
    const textParts: string[] = [];
    let imageCount = 0;
    for (const part of content) {
      if (part.type === "text") {
        textParts.push(String(part.text ?? ""));
      } else if (part.type === "image_url") {
        imageCount += 1;
      }
    }
    return { text: textParts.join(" "), hasImages: imageCount > 0, imageCount };
  }
  return { text: JSON.stringify(content), hasImages: false, imageCount: 0 };
}

/** Parse a metadata payload into a plain object. */
export function parseMeta(meta: unknown): Record<string, unknown> {
  if (!meta) return {};
  if (typeof meta === "string") {
    try {
      return JSON.parse(meta);
    } catch {
      return {};
    }
  }
  if (typeof meta === "object" && meta !== null) {
    return meta as Record<string, unknown>;
  }
  return {};
}

/** Normalize an unknown value into a clean string array. */
export function normalizeStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(String).filter(Boolean);
}

/** Format a unix timestamp for audit rows. */
export function formatTimestamp(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleString();
}

export function ContentDisplay({ content }: { content: unknown }) {
  const { text, hasImages, imageCount } = formatContent(content);
  const display = text.slice(0, 500) + (text.length > 500 ? "..." : "");

  return (
    <>
      {hasImages && (
        <span className="mr-2 inline-flex items-center gap-1 rounded-md border border-blue-500/30 bg-blue-500/15 px-1.5 py-0.5 text-xs font-medium text-blue-400">
          <ImageIcon className="h-3 w-3" />
          {imageCount > 1 ? `${imageCount} images` : "image"}
        </span>
      )}
      {display}
    </>
  );
}

export function MetaBadges({ meta }: { meta: unknown }) {
  const parsed = parseMeta(meta);
  const source = parsed.source as string | undefined;
  const imagesCount = parsed.images_count as number | undefined;
  const files = Array.isArray(parsed.attached_files)
    ? parsed.attached_files.map(String)
    : [];

  if (!source && !imagesCount && files.length === 0) return null;

  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {source === "voice" && (
        <Badge
          className="gap-1 border-orange-500/30 bg-orange-500/15 text-orange-400"
          variant="outline"
        >
          <Mic className="h-3 w-3" />
          voice
        </Badge>
      )}
      {imagesCount != null && imagesCount > 0 && (
        <Badge
          className="gap-1 border-blue-500/30 bg-blue-500/15 text-blue-400"
          variant="outline"
        >
          <ImageIcon className="h-3 w-3" />
          {imagesCount > 1 ? `${imagesCount} images` : "image"}
        </Badge>
      )}
      {files.map((name) => (
        <Badge
          key={name}
          className="gap-1 border-indigo-500/30 bg-indigo-500/15 text-indigo-400"
          variant="outline"
        >
          <FileText className="h-3 w-3" />
          {name}
        </Badge>
      ))}
    </div>
  );
}

export function TypeBadge({ type }: { type: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium",
        TYPE_COLORS[type] || "border-border bg-muted text-muted-foreground"
      )}
    >
      {type}
    </span>
  );
}

function NamedBadge({
  value,
  colors,
}: {
  value: string;
  colors: Record<string, string>;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium",
        colors[value] || "border-border bg-muted text-muted-foreground"
      )}
    >
      {value.replace(/_/g, " ")}
    </span>
  );
}

export function RouteMismatchBadge({ label }: { label: string }) {
  return <NamedBadge value={label} colors={ROUTE_MISMATCH_COLORS} />;
}

export function SummaryStatusBadge({ status }: { status: string }) {
  return <NamedBadge value={status} colors={SUMMARY_STATUS_COLORS} />;
}

export function RecordRelationBadge({ relation }: { relation: string }) {
  return <NamedBadge value={relation} colors={NOTE_RELATION_COLORS} />;
}

export function SummaryScopeBadge({ scope }: { scope: string }) {
  return <NamedBadge value={scope} colors={SUMMARY_SCOPE_COLORS} />;
}

export function AuditEventBadge({ eventType }: { eventType: string }) {
  return <NamedBadge value={eventType} colors={AUDIT_EVENT_COLORS} />;
}

export function RecordsLogBadge({ decision }: { decision: string }) {
  return <NamedBadge value={decision} colors={NOTES_LOG_COLORS} />;
}

export function SummariesLogBadge({ decision }: { decision: string }) {
  return <NamedBadge value={decision} colors={SUMMARIES_LOG_COLORS} />;
}

export function SectionIntro({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}

export function SummaryCard({
  title,
  value,
  detail,
  toneClassName,
  accentClassName,
}: {
  title: string;
  value: string | number;
  detail?: string;
  toneClassName?: string;
  accentClassName?: string;
}) {
  return (
    <Card className="relative overflow-hidden border-border/80 bg-card/95 shadow-sm">
      <div
        className={cn("absolute inset-x-0 top-0 h-1 bg-border/60", accentClassName)}
      />
      <CardHeader className="pb-3">
        <CardDescription className="text-base font-medium text-foreground/75">
          {title}
        </CardDescription>
        <CardTitle
          className={cn("text-5xl font-semibold tracking-tight", toneClassName)}
        >
          {value}
        </CardTitle>
      </CardHeader>
      {detail ? (
        <CardContent>
          <p className="text-sm leading-6 text-muted-foreground">{detail}</p>
        </CardContent>
      ) : null}
    </Card>
  );
}

export function EmptyStateCard({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <Card className="border-dashed">
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
    </Card>
  );
}
