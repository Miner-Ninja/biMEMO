import { useEffect, useState, type ReactNode } from "react";
import type { TranslationKey } from "@/i18n";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Bell,
  Bot,
  Check,
  Clock3,
  Pause,
  Repeat,
  X,
  Zap,
} from "lucide-react";
import type {
  ScheduledTask,
  ScheduledTaskRun,
  TaskRunStatus,
} from "@/api/scheduled-tasks";
import { listTaskRuns, parseAllowedTools } from "@/api/scheduled-tasks";
import {
  describeSchedule,
  formatAbsoluteTime,
  formatRelativeTime,
} from "@/lib/scheduler-format";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

interface TaskDetailsModalProps {
  task: ScheduledTask | null;
  onClose: () => void;
}

export function TaskDetailsModal({ task, onClose }: TaskDetailsModalProps) {
  const { t } = useTranslation();
  const open = task !== null;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        {task && (
          <>
            <DialogHeader>
              <DialogTitle>{task.name}</DialogTitle>
              <DialogDescription>{t("scheduler.taskDetails")}</DialogDescription>
            </DialogHeader>
            <DetailsBody task={task} />
            <DialogFooter className="pt-2">
              <button
                type="button"
                className="inline-flex h-9 items-center justify-center rounded-md border border-input bg-background px-4 text-sm font-medium hover:bg-accent"
                onClick={onClose}
              >
                {t("common.close")}
              </button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function DetailsBody({ task }: { task: ScheduledTask }) {
  const { t } = useTranslation();
  const isReminder = task.task_type === "reminder";
  const allowedTools = parseAllowedTools(task.allowed_tools);
  const scheduleLine = describeSchedule(
    task.schedule_kind,
    task.schedule_expr,
    task.timezone
  );

  return (
    <div className="space-y-5">
      {/* Header chips */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
          {isReminder ? <Bell className="h-3 w-3" /> : <Bot className="h-3 w-3" />}
          {isReminder ? t("scheduler.reminderType") : t("scheduler.agentTaskChip")}
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
          <Clock3 className="h-3 w-3" />
          {scheduleLine}
        </span>
        {task.repeat_count > 0 && (
          <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
            <Repeat className="h-3 w-3" />
            {task.repeat_limit
              ? t("scheduler.repeatCountOf", {
                  count: task.repeat_count,
                  limit: task.repeat_limit,
                })
              : task.repeat_count}
          </span>
        )}
      </div>

      {/* Prompt */}
      <Section title={t("scheduler.promptSection")}>
        <pre className="max-h-60 overflow-y-auto whitespace-pre-wrap rounded-md border border-border bg-muted/40 p-3 font-mono text-xs leading-relaxed text-foreground/90">
          {task.prompt}
        </pre>
      </Section>

      {/* Allowed tools */}
      {!isReminder && (
        <Section title={t("scheduler.allowedToolsLabel")}>
          {allowedTools.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {t("scheduler.noToolsEnabled")}
            </p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {allowedTools.map((tool) => (
                <span
                  key={tool}
                  className="rounded-md border border-border bg-background/60 px-2 py-0.5 font-mono text-[11px] text-foreground/80"
                >
                  {tool}
                </span>
              ))}
            </div>
          )}
        </Section>
      )}

      {/* Last error */}
      {task.last_error && (
        <Section title={t("scheduler.lastErrorSection")}>
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-[11px]">
              {task.last_error}
            </pre>
          </div>
        </Section>
      )}

      {/* Run history */}
      <RunHistorySection taskId={task.id} />

      {/* Metadata grid */}
      <Section title={t("scheduler.metadataSection")}>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 font-mono text-[11px]">
          <MetaRow label={t("scheduler.metaStatus")} value={task.status} />
          <MetaRow label={t("scheduler.metaScheduleKind")} value={task.schedule_kind} />
          <MetaRow label={t("scheduler.metaScheduleExpr")} value={task.schedule_expr} />
          <MetaRow label={t("scheduler.metaTimezone")} value={task.timezone} />
          <MetaRow
            label={t("scheduler.metaNextRun")}
            value={`${formatAbsoluteTime(task.next_run_at)} (${formatRelativeTime(task.next_run_at)})`}
          />
          <MetaRow
            label={t("scheduler.metaLastRun")}
            value={
              task.last_run_at
                ? `${formatAbsoluteTime(task.last_run_at)} (${formatRelativeTime(task.last_run_at)})`
                : "—"
            }
          />
          <MetaRow
            label={t("scheduler.metaCreated")}
            value={formatAbsoluteTime(task.created_at)}
          />
          <MetaRow label={t("scheduler.metaTaskId")} value={task.id} />
        </dl>
      </Section>
    </div>
  );
}

interface SectionProps {
  title: string;
  children: ReactNode;
}

function Section({ title, children }: SectionProps) {
  return (
    <section className="space-y-2">
      <h3 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}

interface MetaRowProps {
  label: string;
  value: string;
}

function MetaRow({ label, value }: MetaRowProps) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="truncate text-foreground/90" title={value}>
        {value}
      </dd>
    </>
  );
}

const STATUS_STYLES: Record<
  TaskRunStatus,
  { labelKey: TranslationKey; icon: typeof Check; className: string }
> = {
  ok: { labelKey: "scheduler.runStatusOk", icon: Check, className: "text-emerald-500" },
  error: {
    labelKey: "scheduler.runStatusError",
    icon: X,
    className: "text-destructive",
  },
  timeout: {
    labelKey: "scheduler.runStatusTimeout",
    icon: AlertTriangle,
    className: "text-amber-500",
  },
  silent: {
    labelKey: "scheduler.runStatusSilent",
    icon: Pause,
    className: "text-muted-foreground",
  },
  skipped: {
    labelKey: "scheduler.runStatusSkipped",
    icon: Zap,
    className: "text-muted-foreground",
  },
};

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

function RunHistorySection({ taskId }: { taskId: string }) {
  const { t } = useTranslation();
  const [runs, setRuns] = useState<ScheduledTaskRun[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setRuns(null);
    setError("");
    listTaskRuns(taskId, 20)
      .then((data) => {
        if (!cancelled) setRuns(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : t("scheduler.loadRunHistoryFailed")
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, t]);

  return (
    <Section title={t("scheduler.runHistorySection")}>
      {error && <p className="text-xs text-destructive">{error}</p>}
      {runs === null && !error && (
        <div className="space-y-1">
          {Array.from({ length: 3 }).map((_, idx) => (
            <div key={idx} className="h-8 animate-pulse rounded-md bg-muted" />
          ))}
        </div>
      )}
      {runs !== null && runs.length === 0 && (
        <p className="text-xs italic text-muted-foreground">
          {t("scheduler.noRunsYet")}
        </p>
      )}
      {runs !== null && runs.length > 0 && (
        <div className="overflow-hidden rounded-md border border-border">
          <table className="w-full text-[11px]">
            <thead className="bg-background/40">
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="px-2.5 py-1.5 font-medium uppercase tracking-[0.06em]">
                  {t("scheduler.metaStatus")}
                </th>
                <th className="px-2.5 py-1.5 font-medium uppercase tracking-[0.06em]">
                  {t("scheduler.runWhenHeader")}
                </th>
                <th className="px-2.5 py-1.5 font-medium uppercase tracking-[0.06em]">
                  {t("scheduler.runDurationHeader")}
                </th>
                <th className="px-2.5 py-1.5 font-medium uppercase tracking-[0.06em]">
                  {t("scheduler.runDetailHeader")}
                </th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run, idx) => {
                const styles = STATUS_STYLES[run.status] ?? STATUS_STYLES.ok;
                const Icon = styles.icon;
                const detail = run.error_message || run.result_text || "";
                return (
                  <tr
                    key={run.id}
                    className={cn(
                      "border-b border-border/40 last:border-0",
                      idx % 2 === 0 ? "bg-background/20" : ""
                    )}
                  >
                    <td className="px-2.5 py-1.5">
                      <span
                        className={cn(
                          "inline-flex items-center gap-1 font-medium",
                          styles.className
                        )}
                      >
                        <Icon className="h-3 w-3" />
                        {t(styles.labelKey)}
                      </span>
                    </td>
                    <td
                      className="px-2.5 py-1.5 font-mono tabular-nums text-foreground/80"
                      title={formatAbsoluteTime(run.started_at)}
                    >
                      {formatRelativeTime(run.started_at)}
                    </td>
                    <td className="px-2.5 py-1.5 font-mono tabular-nums text-foreground/80">
                      {formatDuration(run.duration_ms)}
                    </td>
                    <td
                      className="max-w-[200px] truncate px-2.5 py-1.5 text-muted-foreground"
                      title={detail}
                    >
                      {detail || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
