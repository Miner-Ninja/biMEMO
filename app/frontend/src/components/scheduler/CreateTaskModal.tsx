import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Bell, Bot, Loader2 } from "lucide-react";
import {
  type CreateScheduledTaskPayload,
  type ScheduledTask,
  type ScheduledTaskType,
  type ToolCatalogEntry,
  type UpdateScheduledTaskPayload,
  createScheduledTask,
  listToolCatalog,
  parseAllowedTools,
  updateScheduledTaskFields,
} from "@/api/scheduled-tasks";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  ScheduleBuilder,
  buildScheduleExpression,
  createInitialScheduleValue,
  parseScheduleToBuilder,
  type ScheduleBuilderValue,
} from "./ScheduleBuilder";
import { cn } from "@/lib/utils";

interface CreateTaskModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: (taskId: string, name: string) => void;
  onError: (message: string) => void;
  /** When provided the modal opens in edit mode for this task. */
  task?: ScheduledTask | null;
}

interface FormState {
  name: string;
  task_type: ScheduledTaskType;
  prompt: string;
  schedule: ScheduleBuilderValue;
  allowed_tools: Set<string>;
  repeat_limit: string;
}

function makeInitialForm(): FormState {
  return {
    name: "",
    task_type: "agent_task",
    prompt: "",
    schedule: createInitialScheduleValue(),
    allowed_tools: new Set(),
    repeat_limit: "",
  };
}

function formFromTask(task: ScheduledTask): FormState {
  return {
    name: task.name,
    task_type: task.task_type,
    prompt: task.prompt,
    schedule: parseScheduleToBuilder(task.schedule_kind, task.schedule_expr),
    allowed_tools: new Set(parseAllowedTools(task.allowed_tools)),
    repeat_limit: task.repeat_limit !== null ? String(task.repeat_limit) : "",
  };
}

export function CreateTaskModal({
  open,
  onClose,
  onCreated,
  onError,
  task,
}: CreateTaskModalProps) {
  const { t } = useTranslation();
  const isEdit = task != null;
  const [form, setForm] = useState<FormState>(makeInitialForm);
  const [tools, setTools] = useState<ToolCatalogEntry[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<keyof FormState, string>>>({});

  useEffect(() => {
    if (!open) return;
    setForm(task ? formFromTask(task) : makeInitialForm());
    setErrors({});
    listToolCatalog()
      .then(setTools)
      .catch(() => setTools([]));
  }, [open, task]);

  const isAgent = form.task_type === "agent_task";

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const toggleTool = (name: string) => {
    setForm((prev) => {
      const next = new Set(prev.allowed_tools);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return { ...prev, allowed_tools: next };
    });
  };

  const validate = (): boolean => {
    const next: Partial<Record<keyof FormState, string>> = {};
    if (!form.name.trim()) next.name = t("scheduler.nameRequired");
    if (!form.prompt.trim()) next.prompt = t("scheduler.promptRequired");
    const scheduleExpr = buildScheduleExpression(form.schedule);
    if (!scheduleExpr) next.schedule = t("scheduler.scheduleIncomplete");
    if (form.repeat_limit) {
      const n = Number(form.repeat_limit);
      if (!Number.isFinite(n) || n < 1) {
        next.repeat_limit = t("scheduler.positiveNumberRequired");
      }
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!validate()) return;
    const scheduleExpr = buildScheduleExpression(form.schedule);
    if (!scheduleExpr) return;
    setSubmitting(true);
    try {
      if (isEdit && task) {
        const updatePayload: UpdateScheduledTaskPayload = {
          name: form.name.trim(),
          prompt: form.prompt.trim(),
          schedule: scheduleExpr,
          allowed_tools: isAgent ? Array.from(form.allowed_tools) : [],
          repeat_limit: form.repeat_limit ? Number(form.repeat_limit) : null,
        };
        await updateScheduledTaskFields(task.id, updatePayload);
        onCreated(task.id, form.name.trim());
      } else {
        const payload: CreateScheduledTaskPayload = {
          name: form.name.trim(),
          task_type: form.task_type,
          prompt: form.prompt.trim(),
          schedule: scheduleExpr,
          allowed_tools: isAgent ? Array.from(form.allowed_tools) : [],
          repeat_limit: form.repeat_limit ? Number(form.repeat_limit) : null,
          conversation_id: "",
        };
        const result = await createScheduledTask(payload);
        onCreated(result.task_id, payload.name);
      }
      onClose();
    } catch (err) {
      const fallback = isEdit
        ? t("scheduler.updateTaskFailed")
        : t("scheduler.createTaskFailed");
      onError(err instanceof Error ? err.message : fallback);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !submitting) onClose();
      }}
    >
      <DialogContent className="max-w-5xl gap-3 p-5">
        <DialogHeader className="gap-1">
          <DialogTitle>
            {isEdit ? t("scheduler.editTaskTitle") : t("scheduler.createTaskTitle")}
          </DialogTitle>
          <DialogDescription>
            {isEdit
              ? t("scheduler.editTaskDescription")
              : t("scheduler.createTaskDescription")}
          </DialogDescription>
        </DialogHeader>
        <form id="create-task-form" className="space-y-3" onSubmit={handleSubmit}>
          {/* Type segmented control (only for create — type is locked in edit) */}
          {!isEdit && (
            <div className="grid grid-cols-2 gap-2 rounded-md border border-input bg-background p-1">
              <TypeOption
                icon={<Bot className="h-3.5 w-3.5" />}
                label={t("scheduler.agentTaskType")}
                hint={t("scheduler.agentTaskTypeHint")}
                active={form.task_type === "agent_task"}
                onClick={() => update("task_type", "agent_task")}
              />
              <TypeOption
                icon={<Bell className="h-3.5 w-3.5" />}
                label={t("scheduler.reminderType")}
                hint={t("scheduler.reminderTypeHint")}
                active={form.task_type === "reminder"}
                onClick={() => update("task_type", "reminder")}
              />
            </div>
          )}

          {/* Two-column body: schedule on the left, content on the right */}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {/* Left column: name + schedule builder */}
            <div className="space-y-3">
              <Field label={t("scheduler.nameLabel")} error={errors.name}>
                <input
                  type="text"
                  className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-ring/40 focus-visible:ring-[3px]"
                  placeholder={t("scheduler.namePlaceholder")}
                  value={form.name}
                  onChange={(e) => update("name", e.target.value)}
                  disabled={submitting}
                  maxLength={200}
                />
              </Field>

              <Field label={t("scheduler.scheduleLabel")} error={errors.schedule}>
                <ScheduleBuilder
                  value={form.schedule}
                  onChange={(next) => update("schedule", next)}
                  disabled={submitting}
                />
              </Field>
            </div>

            {/* Right column: prompt + tools + repeat */}
            <div className="space-y-3">
              <Field
                label={
                  isAgent
                    ? t("scheduler.taskInstructionsLabel")
                    : t("scheduler.reminderTextLabel")
                }
                error={errors.prompt}
              >
                <textarea
                  className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm leading-relaxed outline-none focus-visible:border-ring focus-visible:ring-ring/40 focus-visible:ring-[3px] min-h-[80px]"
                  placeholder={
                    isAgent
                      ? t("scheduler.agentPromptPlaceholder")
                      : t("scheduler.reminderPromptPlaceholder")
                  }
                  value={form.prompt}
                  onChange={(e) => update("prompt", e.target.value)}
                  disabled={submitting}
                  rows={3}
                />
              </Field>

              {isAgent && (
                <div className="space-y-2 rounded-md border border-input bg-background/40 p-3">
                  <div className="flex items-baseline justify-between">
                    <span className="text-sm font-medium">
                      {t("scheduler.allowedToolsLabel")}
                    </span>
                    <span className="text-[11px] text-muted-foreground tabular-nums">
                      {t("scheduler.toolsSelected", {
                        count: form.allowed_tools.size,
                      })}
                    </span>
                  </div>
                  {tools.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      {t("scheduler.noToolsAvailable")}
                    </p>
                  ) : (
                    <div className="flex max-h-[160px] flex-wrap gap-1 overflow-y-auto pr-1">
                      {tools.map((tool) => {
                        const active = form.allowed_tools.has(tool.name);
                        return (
                          <button
                            key={tool.name}
                            type="button"
                            onClick={() => toggleTool(tool.name)}
                            title={tool.description}
                            disabled={submitting}
                            className={cn(
                              "inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[11px] transition-colors",
                              active
                                ? "border-primary/40 bg-primary/10 text-primary"
                                : "border-border bg-background text-muted-foreground hover:border-foreground/30 hover:text-foreground"
                            )}
                          >
                            {tool.name}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              <Field
                label={t("scheduler.repeatLimitLabel")}
                hint={t("scheduler.repeatLimitHint")}
                error={errors.repeat_limit}
              >
                <input
                  type="number"
                  min={1}
                  className="h-9 w-32 rounded-md border border-input bg-background px-3 font-mono text-sm tabular-nums outline-none focus-visible:border-ring focus-visible:ring-ring/40 focus-visible:ring-[3px]"
                  placeholder="∞"
                  value={form.repeat_limit}
                  onChange={(e) => update("repeat_limit", e.target.value)}
                  disabled={submitting}
                />
              </Field>
            </div>
          </div>
        </form>
        <DialogFooter className="gap-2">
          <button
            type="button"
            className="inline-flex h-9 items-center justify-center rounded-md border border-input bg-background px-4 text-sm font-medium hover:bg-accent disabled:opacity-50"
            onClick={onClose}
            disabled={submitting}
          >
            {t("common.cancel")}
          </button>
          <button
            type="submit"
            form="create-task-form"
            className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            disabled={submitting}
          >
            {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {submitting
              ? isEdit
                ? t("scheduler.savingButton")
                : t("scheduler.creatingButton")
              : isEdit
                ? t("scheduler.saveChangesButton")
                : t("scheduler.createTaskButton")}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface FieldProps {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}

function Field({ label, hint, error, children }: FieldProps) {
  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-foreground">{label}</label>
      {children}
      {error ? (
        <p className="text-[11px] text-destructive">{error}</p>
      ) : hint ? (
        <p className="text-[11px] text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

interface TypeOptionProps {
  icon: ReactNode;
  label: string;
  hint: string;
  active: boolean;
  onClick: () => void;
}

function TypeOption({ icon, label, hint, active, onClick }: TypeOptionProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex items-center gap-2 rounded-md px-3 py-1.5 text-left transition-all",
        active
          ? "bg-foreground text-background shadow-sm"
          : "text-muted-foreground hover:bg-accent hover:text-foreground"
      )}
    >
      <span className="flex items-center gap-1.5 text-xs font-semibold">
        {icon}
        {label}
      </span>
      <span
        className={cn(
          "text-[10px]",
          active ? "text-background/70" : "text-muted-foreground"
        )}
      >
        · {hint}
      </span>
    </button>
  );
}
