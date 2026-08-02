import { useEffect, useState } from "react";
import {
  Bell,
  Bot,
  Check,
  Clock3,
  Eye,
  Pause,
  Pencil,
  Play,
  Repeat,
  Trash2,
  X,
} from "lucide-react";
import type { ScheduledTask } from "@/api/scheduled-tasks";
import { parseAllowedTools } from "@/api/scheduled-tasks";
import {
  describeSchedule,
  formatRelativeTime,
  nextRunProgress,
} from "@/lib/scheduler-format";
import { cn } from "@/lib/utils";

interface ScheduledTaskCardProps {
  task: ScheduledTask;
  onPauseToggle: (task: ScheduledTask) => void;
  onDelete: (task: ScheduledTask) => void;
  onView: (task: ScheduledTask) => void;
  onEdit: (task: ScheduledTask) => void;
}

const STATUS_RAIL_CLASS: Record<ScheduledTask["status"], string> = {
  active: "bg-emerald-500",
  paused: "bg-amber-500",
  completed: "bg-muted-foreground/40",
};

const STATUS_LABEL: Record<ScheduledTask["status"], string> = {
  active: "Active",
  paused: "Paused",
  completed: "Done",
};

const STATUS_TEXT: Record<ScheduledTask["status"], string> = {
  active: "text-emerald-500",
  paused: "text-amber-500",
  completed: "text-muted-foreground",
};

export function ScheduledTaskCard({
  task,
  onPauseToggle,
  onDelete,
  onView,
  onEdit,
}: ScheduledTaskCardProps) {
  // Re-render every 30 seconds so relative times stay fresh
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 30000);
    return () => clearInterval(id);
  }, []);

  const isReminder = task.task_type === "reminder";
  const isCompleted = task.status === "completed";
  const isPaused = task.status === "paused";
  const allowedTools = parseAllowedTools(task.allowed_tools);
  const scheduleLine = describeSchedule(
    task.schedule_kind,
    task.schedule_expr,
    task.timezone
  );
  const nextRel = formatRelativeTime(task.next_run_at);
  const lastRel = task.last_run_at ? formatRelativeTime(task.last_run_at) : "";
  const progress =
    isCompleted || isPaused ? 0 : nextRunProgress(task.next_run_at, task.last_run_at);

  return (
    <div
      className={cn(
        "group relative flex flex-col overflow-hidden rounded-xl border border-border bg-card shadow-xs transition-all",
        "hover:border-foreground/20 hover:shadow-sm",
        isCompleted && "opacity-70"
      )}
    >
      {/* Status rail */}
      <div
        aria-hidden
        className={cn(
          "absolute left-0 top-0 h-full w-[3px]",
          STATUS_RAIL_CLASS[task.status]
        )}
      />

      <div className="flex flex-col gap-4 p-5 pl-6">
        {/* Top row: type + status */}
        <div className="flex items-start justify-between gap-3">
          <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
            {isReminder ? <Bell className="h-3 w-3" /> : <Bot className="h-3 w-3" />}
            {isReminder ? "Reminder" : "Agent task"}
          </span>
          <span
            className={cn(
              "text-[10px] font-semibold uppercase tracking-[0.12em]",
              STATUS_TEXT[task.status]
            )}
          >
            {STATUS_LABEL[task.status]}
          </span>
        </div>

        {/* Title */}
        <h3
          className="text-base font-semibold leading-snug text-foreground"
          title={task.name}
        >
          <span className="line-clamp-2">{task.name}</span>
        </h3>

        {/* Schedule */}
        <div className="flex items-center gap-2 text-xs">
          <Clock3 className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="font-mono text-foreground/90">{scheduleLine}</span>
        </div>

        {/* Next run with progress rail */}
        {!isCompleted && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-[11px] text-muted-foreground">
              <span className="uppercase tracking-[0.08em]">Next run</span>
              <span className="font-mono tabular-nums text-foreground/80">
                {isPaused ? "paused" : nextRel || "pending"}
              </span>
            </div>
            <div className="h-[2px] w-full overflow-hidden rounded-full bg-muted">
              <div
                className={cn(
                  "h-full transition-all duration-700 ease-out",
                  isPaused ? "bg-amber-500/40" : "bg-foreground/60"
                )}
                style={{ width: `${Math.round(progress * 100)}%` }}
              />
            </div>
          </div>
        )}

        {/* Last run + repeat counter row */}
        {(task.last_run_at || task.repeat_count > 0) && (
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            {task.last_run_at ? (
              <span className="inline-flex items-center gap-1.5">
                {task.last_status === "error" ? (
                  <X className="h-3 w-3 text-destructive" />
                ) : (
                  <Check className="h-3 w-3 text-emerald-500" />
                )}
                <span>Ran {lastRel}</span>
              </span>
            ) : (
              <span />
            )}
            {task.repeat_count > 0 && (
              <span className="inline-flex items-center gap-1 font-mono tabular-nums">
                <Repeat className="h-3 w-3" />
                {task.repeat_count}
                {task.repeat_limit ? `/${task.repeat_limit}` : ""}
              </span>
            )}
          </div>
        )}

        {/* Prompt preview */}
        <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">
          {task.prompt}
        </p>

        {/* Allowed tools (only when there are any) */}
        {allowedTools.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {allowedTools.slice(0, 4).map((tool) => (
              <span
                key={tool}
                className="rounded-sm border border-border/60 bg-background/40 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
              >
                {tool}
              </span>
            ))}
            {allowedTools.length > 4 && (
              <span className="rounded-sm border border-border/60 bg-background/40 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                +{allowedTools.length - 4}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Actions row */}
      <div className="mt-auto flex items-center justify-end gap-1 border-t border-border/60 bg-background/40 px-3 py-2 pl-6">
        <button
          type="button"
          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          onClick={() => onView(task)}
          title="View details"
          aria-label="View details"
        >
          <Eye className="h-3.5 w-3.5" />
        </button>
        {!isCompleted && (
          <button
            type="button"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            onClick={() => onEdit(task)}
            title="Edit"
            aria-label="Edit"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
        )}
        {!isCompleted && (
          <button
            type="button"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            onClick={() => onPauseToggle(task)}
            title={isPaused ? "Resume" : "Pause"}
            aria-label={isPaused ? "Resume" : "Pause"}
          >
            {isPaused ? (
              <Play className="h-3.5 w-3.5" />
            ) : (
              <Pause className="h-3.5 w-3.5" />
            )}
          </button>
        )}
        <button
          type="button"
          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
          onClick={() => onDelete(task)}
          title="Delete"
          aria-label="Delete"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
