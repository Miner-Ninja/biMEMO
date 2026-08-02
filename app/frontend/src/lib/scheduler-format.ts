import { format, formatDistanceToNow, parseISO } from "date-fns";
import type { ScheduleKind } from "@/api/scheduled-tasks";

const INTERVAL_RE = /^(\d+)\s*(m|h|d)$/i;
const UNIT_LABELS: Record<string, [string, string]> = {
  m: ["minute", "minutes"],
  h: ["hour", "hours"],
  d: ["day", "days"],
};

function parseSafe(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  try {
    const date = parseISO(iso);
    return Number.isNaN(date.getTime()) ? null : date;
  } catch {
    return null;
  }
}

export function formatRelativeTime(iso: string | null | undefined): string {
  const date = parseSafe(iso);
  if (!date) return "";
  return formatDistanceToNow(date, { addSuffix: true });
}

export function formatAbsoluteTime(iso: string | null | undefined): string {
  const date = parseSafe(iso);
  if (!date) return "";
  return format(date, "MMM d, HH:mm");
}

export function describeSchedule(
  kind: ScheduleKind,
  expr: string,
  timezone: string
): string {
  const trimmed = expr.trim();
  if (kind === "interval") {
    const match = INTERVAL_RE.exec(trimmed);
    if (match) {
      const value = Number(match[1]);
      const unit = match[2].toLowerCase();
      const [singular, plural] = UNIT_LABELS[unit];
      return `Every ${value} ${value === 1 ? singular : plural}`;
    }
    return `Every ${trimmed}`;
  }
  if (kind === "cron") {
    const tz = timezone && timezone !== "UTC" ? ` (${timezone})` : "";
    return `Cron · ${trimmed}${tz}`;
  }
  // once
  return `Once · ${formatAbsoluteTime(trimmed) || trimmed}`;
}

/**
 * Returns a 0..1 progress value showing how close `next` is.
 * Uses `last` as the start of the window when available, otherwise
 * falls back to a 1-hour look-back from `next`.
 */
export function nextRunProgress(
  next: string | null | undefined,
  last: string | null | undefined
): number {
  const target = parseSafe(next)?.getTime();
  if (target === undefined) return 0;
  const lastDate = parseSafe(last);
  const start = lastDate ? lastDate.getTime() : target - 60 * 60 * 1000;
  if (start >= target) return 0;
  const now = Date.now();
  if (now <= start) return 0;
  if (now >= target) return 1;
  return (now - start) / (target - start);
}
