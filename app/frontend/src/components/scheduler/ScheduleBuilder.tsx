import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { CalendarDays, Clock3, Repeat, Wrench } from "lucide-react";
import i18n, { type TranslationKey } from "@/i18n";
import { cn } from "@/lib/utils";

export type ScheduleMode = "once" | "every" | "daily" | "advanced";

export interface ScheduleBuilderValue {
  mode: ScheduleMode;
  // once
  onceDate: string; // YYYY-MM-DD
  onceTime: string; // HH:MM
  // every
  everyValue: string;
  everyUnit: "m" | "h" | "d";
  // daily / weekly
  dailyTime: string; // HH:MM
  dailyDays: Set<number>; // 0=Sun..6=Sat
  // advanced (raw cron)
  advancedExpr: string;
}

interface ScheduleBuilderProps {
  value: ScheduleBuilderValue;
  onChange: (value: ScheduleBuilderValue) => void;
  disabled?: boolean;
}

const WEEKDAYS: Array<{ idx: number; labelKey: TranslationKey }> = [
  { idx: 1, labelKey: "scheduler.weekdayMon" },
  { idx: 2, labelKey: "scheduler.weekdayTue" },
  { idx: 3, labelKey: "scheduler.weekdayWed" },
  { idx: 4, labelKey: "scheduler.weekdayThu" },
  { idx: 5, labelKey: "scheduler.weekdayFri" },
  { idx: 6, labelKey: "scheduler.weekdaySat" },
  { idx: 0, labelKey: "scheduler.weekdaySun" },
];

const MODE_TABS: Array<{
  key: ScheduleMode;
  labelKey: TranslationKey;
  icon: typeof Clock3;
}> = [
  { key: "once", labelKey: "scheduler.modeOnce", icon: CalendarDays },
  { key: "every", labelKey: "scheduler.modeEvery", icon: Repeat },
  { key: "daily", labelKey: "scheduler.modeDaily", icon: Clock3 },
  { key: "advanced", labelKey: "scheduler.modeAdvanced", icon: Wrench },
];

export function createInitialScheduleValue(): ScheduleBuilderValue {
  const now = new Date();
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const isoDate = tomorrow.toISOString().slice(0, 10);
  return {
    mode: "daily",
    onceDate: isoDate,
    onceTime: "09:00",
    everyValue: "30",
    everyUnit: "m",
    dailyTime: "09:00",
    dailyDays: new Set([1, 2, 3, 4, 5]),
    advancedExpr: "",
  };
}

const SIMPLE_INTERVAL_RE = /^(\d+)\s*(m|h|d)$/i;
const SIMPLE_DAILY_CRON_RE = /^(\d+) (\d+) \* \* (\*|[0-9,]+)$/;

/**
 * Parse a stored schedule_kind/expr back into the visual builder state.
 * Used by the edit flow to pre-fill the form. Falls back to the
 * Advanced tab when the expression cannot be mapped onto a simple mode.
 */
export function parseScheduleToBuilder(
  kind: "once" | "interval" | "cron",
  expr: string
): ScheduleBuilderValue {
  const base = createInitialScheduleValue();
  if (kind === "once") {
    // ISO timestamp like "2026-04-10T09:00:00" or with TZ
    const [datePart, timePart = "09:00"] = expr.split("T");
    const time = timePart.slice(0, 5);
    return { ...base, mode: "once", onceDate: datePart, onceTime: time };
  }
  if (kind === "interval") {
    const m = SIMPLE_INTERVAL_RE.exec(expr.trim());
    if (m) {
      return {
        ...base,
        mode: "every",
        everyValue: m[1],
        everyUnit: m[2].toLowerCase() as ScheduleBuilderValue["everyUnit"],
      };
    }
    return { ...base, mode: "advanced", advancedExpr: expr };
  }
  if (kind === "cron") {
    // Try to recognise daily cron "M H * * DOW"
    const m = SIMPLE_DAILY_CRON_RE.exec(expr.trim());
    if (m) {
      const minute = Number(m[1]);
      const hour = Number(m[2]);
      const dowField = m[3];
      const days = new Set<number>();
      if (dowField === "*") {
        for (let i = 0; i < 7; i += 1) days.add(i);
      } else {
        for (const d of dowField.split(",")) {
          const idx = Number(d);
          if (Number.isFinite(idx) && idx >= 0 && idx <= 6) days.add(idx);
        }
      }
      const hh = hour.toString().padStart(2, "0");
      const mm = minute.toString().padStart(2, "0");
      return {
        ...base,
        mode: "daily",
        dailyTime: `${hh}:${mm}`,
        dailyDays: days,
      };
    }
    return { ...base, mode: "advanced", advancedExpr: expr };
  }
  return base;
}

/**
 * Convert visual builder state into a backend schedule expression.
 * Returns one of:
 *  - ISO timestamp for "once"
 *  - "30m" / "2h" / "1d" for "every"
 *  - cron expression for "daily" / "advanced"
 */
export function buildScheduleExpression(v: ScheduleBuilderValue): string | null {
  if (v.mode === "once") {
    if (!v.onceDate || !v.onceTime) return null;
    return `${v.onceDate}T${v.onceTime}:00`;
  }
  if (v.mode === "every") {
    const num = Number(v.everyValue);
    if (!Number.isFinite(num) || num < 1) return null;
    return `${num}${v.everyUnit}`;
  }
  if (v.mode === "daily") {
    if (!v.dailyTime) return null;
    const [hh, mm] = v.dailyTime.split(":");
    if (!hh || !mm) return null;
    const h = Number(hh);
    const m = Number(mm);
    if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
    if (v.dailyDays.size === 0) return null;
    const dow =
      v.dailyDays.size === 7
        ? "*"
        : Array.from(v.dailyDays)
            .sort((a, b) => a - b)
            .join(",");
    return `${m} ${h} * * ${dow}`;
  }
  if (v.mode === "advanced") {
    return v.advancedExpr.trim() || null;
  }
  return null;
}

/**
 * Render a natural-language preview of the schedule for end users.
 * Returns an empty string if the schedule is incomplete.
 */
export function describeBuilderSchedule(v: ScheduleBuilderValue): string {
  if (v.mode === "once") {
    if (!v.onceDate || !v.onceTime) return "";
    try {
      const date = new Date(`${v.onceDate}T${v.onceTime}:00`);
      const formatted = date.toLocaleString(undefined, {
        weekday: "long",
        year: "numeric",
        month: "long",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
      return i18n.t("scheduler.previewOnce", { date: formatted });
    } catch {
      return "";
    }
  }
  if (v.mode === "every") {
    const num = Number(v.everyValue);
    if (!Number.isFinite(num) || num < 1) return "";
    if (v.everyUnit === "m") {
      return i18n.t("scheduler.previewEveryMinutes", { count: num });
    }
    if (v.everyUnit === "h") {
      return i18n.t("scheduler.previewEveryHours", { count: num });
    }
    return i18n.t("scheduler.previewEveryDays", { count: num });
  }
  if (v.mode === "daily") {
    if (!v.dailyTime || v.dailyDays.size === 0) return "";
    const [hh, mm] = v.dailyTime.split(":");
    const time = `${hh}:${mm}`;
    if (v.dailyDays.size === 7) {
      return i18n.t("scheduler.previewDailyAllDays", { time });
    }
    const weekdayOnly =
      v.dailyDays.size === 5 && [1, 2, 3, 4, 5].every((d) => v.dailyDays.has(d));
    if (weekdayOnly) return i18n.t("scheduler.previewWeekdays", { time });
    const weekendOnly =
      v.dailyDays.size === 2 && v.dailyDays.has(0) && v.dailyDays.has(6);
    if (weekendOnly) return i18n.t("scheduler.previewWeekends", { time });
    const names = WEEKDAYS.filter((w) => v.dailyDays.has(w.idx)).map((w) =>
      i18n.t(w.labelKey)
    );
    return i18n.t("scheduler.previewOnDays", { days: names.join(", "), time });
  }
  if (v.mode === "advanced") {
    if (!v.advancedExpr.trim()) return "";
    return i18n.t("scheduler.previewCron", { expr: v.advancedExpr.trim() });
  }
  return "";
}

export function ScheduleBuilder({ value, onChange, disabled }: ScheduleBuilderProps) {
  const { t } = useTranslation();
  const update = <K extends keyof ScheduleBuilderValue>(
    key: K,
    next: ScheduleBuilderValue[K]
  ) => {
    onChange({ ...value, [key]: next });
  };

  const setMode = (mode: ScheduleMode) => {
    onChange({ ...value, mode });
  };

  const toggleDay = (idx: number) => {
    const next = new Set(value.dailyDays);
    if (next.has(idx)) {
      next.delete(idx);
    } else {
      next.add(idx);
    }
    update("dailyDays", next);
  };

  return (
    <div className="space-y-2">
      {/* Mode tabs */}
      <div className="grid grid-cols-4 gap-1 rounded-md border border-input bg-background p-1">
        {MODE_TABS.map((tab) => {
          const Icon = tab.icon;
          const active = value.mode === tab.key;
          return (
            <button
              key={tab.key}
              type="button"
              onClick={() => setMode(tab.key)}
              disabled={disabled}
              className={cn(
                "inline-flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-all",
                active
                  ? "bg-foreground text-background shadow-sm"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground"
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {t(tab.labelKey)}
            </button>
          );
        })}
      </div>

      {/* Mode body */}
      <div className="rounded-md border border-input bg-background/40 p-3">
        {value.mode === "once" && (
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[160px]">
              <label className="mb-1 block text-[11px] uppercase tracking-[0.08em] text-muted-foreground">
                {t("scheduler.dateLabel")}
              </label>
              <input
                type="date"
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-ring/40 focus-visible:ring-[3px]"
                value={value.onceDate}
                onChange={(e) => update("onceDate", e.target.value)}
                disabled={disabled}
              />
            </div>
            <div className="w-32">
              <label className="mb-1 block text-[11px] uppercase tracking-[0.08em] text-muted-foreground">
                {t("scheduler.timeLabel")}
              </label>
              <input
                type="time"
                className="h-9 w-full rounded-md border border-input bg-background px-3 font-mono text-sm tabular-nums outline-none focus-visible:border-ring focus-visible:ring-ring/40 focus-visible:ring-[3px]"
                value={value.onceTime}
                onChange={(e) => update("onceTime", e.target.value)}
                disabled={disabled}
              />
            </div>
          </div>
        )}

        {value.mode === "every" && (
          <div className="flex flex-wrap items-end gap-2">
            <div>
              <label className="mb-1 block text-[11px] uppercase tracking-[0.08em] text-muted-foreground">
                {t("scheduler.modeEvery")}
              </label>
              <input
                type="number"
                min={1}
                className="h-9 w-24 rounded-md border border-input bg-background px-3 font-mono text-sm tabular-nums outline-none focus-visible:border-ring focus-visible:ring-ring/40 focus-visible:ring-[3px]"
                value={value.everyValue}
                onChange={(e) => update("everyValue", e.target.value)}
                disabled={disabled}
              />
            </div>
            <div>
              <label className="mb-1 block text-[11px] uppercase tracking-[0.08em] text-muted-foreground">
                {t("scheduler.unitLabel")}
              </label>
              <select
                className="h-9 rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-ring/40 focus-visible:ring-[3px]"
                value={value.everyUnit}
                onChange={(e) =>
                  update(
                    "everyUnit",
                    e.target.value as ScheduleBuilderValue["everyUnit"]
                  )
                }
                disabled={disabled}
              >
                <option value="m">{t("scheduler.unitMinutes")}</option>
                <option value="h">{t("scheduler.unitHours")}</option>
                <option value="d">{t("scheduler.unitDays")}</option>
              </select>
            </div>
          </div>
        )}

        {value.mode === "daily" && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-end gap-2">
              <div className="w-32">
                <label className="mb-1 block text-[11px] uppercase tracking-[0.08em] text-muted-foreground">
                  {t("scheduler.timeLabel")}
                </label>
                <input
                  type="time"
                  className="h-9 w-full rounded-md border border-input bg-background px-3 font-mono text-sm tabular-nums outline-none focus-visible:border-ring focus-visible:ring-ring/40 focus-visible:ring-[3px]"
                  value={value.dailyTime}
                  onChange={(e) => update("dailyTime", e.target.value)}
                  disabled={disabled}
                />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-[11px] uppercase tracking-[0.08em] text-muted-foreground">
                {t("scheduler.unitDays")}
              </label>
              <div className="flex flex-wrap gap-1">
                {WEEKDAYS.map((day) => {
                  const active = value.dailyDays.has(day.idx);
                  return (
                    <button
                      key={day.idx}
                      type="button"
                      onClick={() => toggleDay(day.idx)}
                      disabled={disabled}
                      className={cn(
                        "h-7 w-11 rounded-md border text-[11px] font-medium transition-colors",
                        active
                          ? "border-foreground bg-foreground text-background"
                          : "border-border bg-background text-muted-foreground hover:border-foreground/30 hover:text-foreground"
                      )}
                    >
                      {t(day.labelKey)}
                    </button>
                  );
                })}
              </div>
              <div className="mt-1.5 flex gap-2 text-[11px]">
                <PresetButton
                  label={t("scheduler.presetAllDays")}
                  onClick={() => update("dailyDays", new Set([0, 1, 2, 3, 4, 5, 6]))}
                  disabled={disabled}
                />
                <PresetButton
                  label={t("scheduler.presetWeekdays")}
                  onClick={() => update("dailyDays", new Set([1, 2, 3, 4, 5]))}
                  disabled={disabled}
                />
                <PresetButton
                  label={t("scheduler.presetWeekends")}
                  onClick={() => update("dailyDays", new Set([0, 6]))}
                  disabled={disabled}
                />
              </div>
            </div>
          </div>
        )}

        {value.mode === "advanced" && (
          <div>
            <label className="mb-1 block text-[11px] uppercase tracking-[0.08em] text-muted-foreground">
              {t("scheduler.cronExpressionLabel")}
            </label>
            <input
              type="text"
              className="h-9 w-full rounded-md border border-input bg-background px-3 font-mono text-sm outline-none focus-visible:border-ring focus-visible:ring-ring/40 focus-visible:ring-[3px]"
              placeholder="0 9 * * *"
              value={value.advancedExpr}
              onChange={(e) => update("advancedExpr", e.target.value)}
              disabled={disabled}
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              {t("scheduler.cronHint")}
            </p>
          </div>
        )}
      </div>

      {/* Natural language preview */}
      <NaturalLanguagePreview value={value} />
    </div>
  );
}

function NaturalLanguagePreview({ value }: { value: ScheduleBuilderValue }) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  useEffect(() => {
    setText(describeBuilderSchedule(value));
  }, [value]);

  if (!text) {
    return (
      <p className="text-[11px] italic text-muted-foreground">
        {t("scheduler.configureScheduleAbove")}
      </p>
    );
  }

  return (
    <p className="text-xs text-foreground/80">
      <span className="text-muted-foreground">{t("scheduler.previewLabel")}</span>
      {text}
    </p>
  );
}

interface PresetButtonProps {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

function PresetButton({ label, onClick, disabled }: PresetButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
    >
      {label}
    </button>
  );
}
