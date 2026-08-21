import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { TranslationKey } from "@/i18n";
import { useTranslation } from "react-i18next";
import { CalendarClock, Plus, RefreshCw } from "lucide-react";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { ScheduledTaskCard } from "@/components/scheduler/ScheduledTaskCard";
import { CreateTaskModal } from "@/components/scheduler/CreateTaskModal";
import { TaskDetailsModal } from "@/components/scheduler/TaskDetailsModal";
import { ToastContainer, useToasts } from "@/components/ui/Toast";
import { useRealtimeStream, type RealtimeEvent } from "@/hooks/useRealtimeStream";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  type ScheduledTask,
  type ScheduledTaskFilter,
  type ScheduledTaskStatus,
  deleteScheduledTask,
  listScheduledTasks,
  updateScheduledTaskStatus,
} from "@/api/scheduled-tasks";
import { cn } from "@/lib/utils";
import { queryClient } from "@/lib/query-client";
import { QUERY_KEYS, errorToDisplayText } from "@/hooks/use-server-state";

const FILTERS: Array<{ key: ScheduledTaskFilter; labelKey: TranslationKey }> = [
  { key: "all", labelKey: "scheduler.filterAll" },
  { key: "active", labelKey: "scheduler.filterActive" },
  { key: "paused", labelKey: "scheduler.filterPaused" },
  { key: "completed", labelKey: "scheduler.filterCompleted" },
];

export function ScheduledTasksPage() {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<ScheduledTaskFilter>("all");
  const [refreshing, setRefreshing] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTask, setEditTask] = useState<ScheduledTask | null>(null);
  const [detailsTask, setDetailsTask] = useState<ScheduledTask | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ScheduledTask | null>(null);
  const { toasts, addToast, dismissToast } = useToasts();

  const {
    data: tasksData,
    isInitialLoading,
    refetch,
    error: queryError,
    errorUpdateCount,
  } = useQuery({
    queryKey: QUERY_KEYS.scheduledTasks(filter),
    queryFn: () => listScheduledTasks(filter),
  });
  const loading = isInitialLoading;
  const tasks = useMemo(() => tasksData ?? [], [tasksData]);
  // Toast once per new failure of the active filter's list; the
  // baseline ref suppresses toasts for errors already in the cache
  // when the page (re)mounts.
  const errorCountRef = useRef(errorUpdateCount);
  useEffect(() => {
    if (errorUpdateCount > errorCountRef.current && queryError !== null) {
      errorCountRef.current = errorUpdateCount;
      addToast({ type: "error", message: errorToDisplayText(queryError, t) });
    }
  }, [errorUpdateCount, queryError, addToast, t]);

  // Listen for scheduled_task_result realtime events to refresh the list
  // and show a toast when a task fires.
  const handleRealtimeEvent = useCallback(
    (event: RealtimeEvent) => {
      if (event.type !== "scheduled_task_result") return;
      const eventPayload =
        event.data && typeof event.data === "object"
          ? (event.data as Record<string, unknown>)
          : null;
      const taskName = String(
        eventPayload?.["task_name"] || t("scheduler.scheduledTaskFallbackName")
      );
      const taskType = String(eventPayload?.["task_type"] || "");
      const resultText = String(eventPayload?.["result_text"] || "");
      const status = String(eventPayload?.["status"] || "ok");
      const isError = status === "error";
      const summary =
        resultText.length > 200 ? resultText.slice(0, 200) + "..." : resultText;
      addToast({
        type: isError ? "error" : "success",
        message:
          taskType === "reminder"
            ? t("scheduler.reminderResultToast", { name: taskName, summary })
            : t("scheduler.taskResultToast", { name: taskName, summary }),
      });
      // Refresh the task list so last_run_at, status, history update live.
      void queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
    },
    [addToast, t]
  );

  const { connect, disconnect } = useRealtimeStream(handleRealtimeEvent, null);

  useEffect(() => {
    void connect();
    return () => disconnect();
  }, [connect, disconnect]);

  const counts = useMemo(() => {
    return {
      total: tasks.length,
    };
  }, [tasks]);

  const pauseMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: ScheduledTaskStatus }) =>
      updateScheduledTaskStatus(id, status),
    onSuccess: (_result, vars) => {
      addToast({
        type: "success",
        message:
          vars.status === "paused"
            ? t("scheduler.taskPaused")
            : t("scheduler.taskResumed"),
      });
      void queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
    },
    onError: (err) => addToast({ type: "error", message: errorToDisplayText(err, t) }),
  });

  const handlePauseToggle = useCallback(
    (task: ScheduledTask) => {
      const nextStatus: ScheduledTaskStatus =
        task.status === "paused" ? "active" : "paused";
      pauseMutation.mutate({ id: task.id, status: nextStatus });
    },
    [pauseMutation]
  );

  const deleteMutation = useMutation({
    mutationFn: ({ id }: { id: string; name: string }) => deleteScheduledTask(id),
    onSuccess: (_result, vars) => {
      addToast({
        type: "success",
        message: t("scheduler.taskDeleted", { name: vars.name }),
      });
      void queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
    },
    onError: (err) => addToast({ type: "error", message: errorToDisplayText(err, t) }),
  });

  const handleDelete = useCallback(() => {
    if (!pendingDelete) return;
    const target = pendingDelete;
    setPendingDelete(null);
    deleteMutation.mutate({ id: target.id, name: target.name });
  }, [pendingDelete, deleteMutation]);

  const handleCreated = useCallback(
    (_taskId: string, name: string) => {
      addToast({ type: "success", message: t("scheduler.taskCreated", { name }) });
      void queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
    },
    [addToast, t]
  );

  return (
    <AdminLayout title={t("scheduler.scheduledTasksTitle")}>
      <div className="flex-1 overflow-y-auto p-6">
        {/* Header */}
        <header className="mb-8 space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-1">
              <h1 className="text-2xl font-semibold tracking-tight">
                {t("scheduler.scheduledTasksTitle")}
              </h1>
              <p className="text-sm text-muted-foreground">
                {t("scheduler.pageSubtitle")}
              </p>
            </div>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              <Plus className="h-4 w-4" />
              {t("scheduler.newTask")}
            </button>
          </div>

          {/* Filters + counter row */}
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
            <div className="inline-flex items-center gap-1 rounded-lg border border-border bg-background p-1">
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => setFilter(f.key)}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-xs font-medium transition-all",
                    filter === f.key
                      ? "bg-foreground text-background"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  {t(f.labelKey)}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-3 text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
              <span className="font-mono tabular-nums">
                {t("scheduler.taskCount", { count: counts.total })}
              </span>
              <button
                type="button"
                onClick={() => {
                  setRefreshing(true);
                  void refetch().finally(() => setRefreshing(false));
                }}
                disabled={refreshing || loading}
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
                title={t("scheduler.refresh")}
                aria-label={t("scheduler.refresh")}
              >
                <RefreshCw
                  className={cn(
                    "h-3.5 w-3.5",
                    (refreshing || loading) && "animate-spin"
                  )}
                />
              </button>
            </div>
          </div>
        </header>

        {/* Body */}
        {loading ? (
          <SkeletonGrid />
        ) : tasks.length === 0 ? (
          <EmptyState onCreate={() => setCreateOpen(true)} filter={filter} />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {tasks.map((task) => (
              <ScheduledTaskCard
                key={task.id}
                task={task}
                onPauseToggle={handlePauseToggle}
                onDelete={(t) => setPendingDelete(t)}
                onView={(t) => setDetailsTask(t)}
                onEdit={(t) => setEditTask(t)}
              />
            ))}
          </div>
        )}
      </div>

      <CreateTaskModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={handleCreated}
        onError={(message) => addToast({ type: "error", message })}
      />

      <CreateTaskModal
        open={editTask !== null}
        task={editTask}
        onClose={() => setEditTask(null)}
        onCreated={(_id, name) => {
          addToast({ type: "success", message: t("scheduler.taskUpdated", { name }) });
          void queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
        }}
        onError={(message) => addToast({ type: "error", message })}
      />

      <TaskDetailsModal task={detailsTask} onClose={() => setDetailsTask(null)} />

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("scheduler.deleteTaskConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDelete
                ? t("scheduler.deleteTaskConfirmDescription", {
                    name: pendingDelete.name,
                  })
                : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={handleDelete}
            >
              {t("common.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </AdminLayout>
  );
}

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, idx) => (
        <div
          key={idx}
          className="relative flex flex-col gap-4 overflow-hidden rounded-xl border border-border bg-card p-5 pl-6 shadow-xs"
        >
          <div className="absolute left-0 top-0 h-full w-[3px] animate-pulse bg-muted" />
          <div className="flex justify-between">
            <div className="h-4 w-20 animate-pulse rounded bg-muted" />
            <div className="h-4 w-12 animate-pulse rounded bg-muted" />
          </div>
          <div className="h-5 w-3/4 animate-pulse rounded bg-muted" />
          <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
          <div className="space-y-1.5">
            <div className="h-3 w-full animate-pulse rounded bg-muted" />
            <div className="h-[2px] w-full animate-pulse rounded bg-muted" />
          </div>
          <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

interface EmptyStateProps {
  onCreate: () => void;
  filter: ScheduledTaskFilter;
}

const EMPTY_TITLE_KEYS: Record<Exclude<ScheduledTaskFilter, "all">, TranslationKey> = {
  active: "scheduler.emptyTitleActive",
  paused: "scheduler.emptyTitlePaused",
  completed: "scheduler.emptyTitleCompleted",
};

function EmptyState({ onCreate, filter }: EmptyStateProps) {
  const { t } = useTranslation();
  const isFiltered = filter !== "all";
  return (
    <div className="flex min-h-[400px] flex-col items-center justify-center gap-5 rounded-xl border border-dashed border-border py-12 text-center">
      <div className="relative">
        <div className="absolute inset-0 -m-2 rounded-full bg-foreground/5 blur-xl" />
        <CalendarClock className="relative h-12 w-12 text-muted-foreground" />
      </div>
      <div className="space-y-1.5 max-w-sm">
        <h2 className="text-base font-semibold">
          {filter !== "all"
            ? t(EMPTY_TITLE_KEYS[filter])
            : t("scheduler.emptyTitleNoTasks")}
        </h2>
        <p className="text-sm text-muted-foreground">
          {isFiltered ? t("scheduler.emptyFilteredHint") : t("scheduler.emptyHint")}
        </p>
      </div>
      <button
        type="button"
        onClick={onCreate}
        className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
      >
        <Plus className="h-4 w-4" />
        {isFiltered ? t("scheduler.createNewTask") : t("scheduler.createFirstTask")}
      </button>
    </div>
  );
}
