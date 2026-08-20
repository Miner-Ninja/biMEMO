import { format, formatDistanceToNow, parseISO } from "date-fns";
import i18n, { getDateFnsLocale } from "@/i18n";
import type { ScheduleKind } from "@/api/scheduled-tasks";

const INTERVAL_RE = /^(\d+)\s*(m|h|d)$/i;
const UNIT_KEYS = {
  m: "schedule.everyMinute",
  h: "schedule.everyHour",
  d: "schedule.everyDay",
} as const;

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
  return formatDistanceToNow(date, { addSuffix: true, locale: getDateFnsLocale() });
}

export function formatAbsoluteTime(iso: string | null | undefined): string {
  const date = parseSafe(iso);
  if (!date) return "";
  return format(date, i18n.t("dates.absolute"), { locale: getDateFnsLocale() });
}

export function describeSchedule(
  kind: ScheduleKind,
  expr: string,
  timezone: string
): string {
  const trimmed = expr.trim();
  if (kind === "interval") {
    const match = INTERVAL_RE.exec(trimmed);
    if (match && match[2]) {
      const value = Number(match[1]);
      const unit = match[2].toLowerCase() as keyof typeof UNIT_KEYS;
      return i18n.t(UNIT_KEYS[unit], { count: value });
    }
    return i18n.t("schedule.every", { expr: trimmed });
  }
  if (kind === "cron") {
    const tz = timezone && timezone !== "UTC" ? ` (${timezone})` : "";
    return i18n.t("schedule.cron", { expr: trimmed }) + tz;
  }
  // once
  return i18n.t("schedule.once", { when: formatAbsoluteTime(trimmed) || trimmed });
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
