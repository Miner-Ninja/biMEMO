import { useCallback, useEffect, useMemo, useState } from 'react'
import { CalendarClock, Plus, RefreshCw } from 'lucide-react'
import { AdminLayout } from '@/components/layout/AdminLayout'
import { ScheduledTaskCard } from '@/components/scheduler/ScheduledTaskCard'
import { CreateTaskModal } from '@/components/scheduler/CreateTaskModal'
import { TaskDetailsModal } from '@/components/scheduler/TaskDetailsModal'
import { ToastContainer, useToasts } from '@/components/ui/Toast'
import type { SSEEvent } from '@/hooks/useSSE'
import { useRealtimeStream } from '@/hooks/useRealtimeStream'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  type ScheduledTask,
  type ScheduledTaskFilter,
  deleteScheduledTask,
  listScheduledTasks,
  updateScheduledTaskStatus,
} from '@/api/scheduled-tasks'
import { cn } from '@/lib/utils'

const FILTERS: Array<{ key: ScheduledTaskFilter; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'active', label: 'Active' },
  { key: 'paused', label: 'Paused' },
  { key: 'completed', label: 'Completed' },
]

export function ScheduledTasksPage() {
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [filter, setFilter] = useState<ScheduledTaskFilter>('all')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editTask, setEditTask] = useState<ScheduledTask | null>(null)
  const [detailsTask, setDetailsTask] = useState<ScheduledTask | null>(null)
  const [pendingDelete, setPendingDelete] = useState<ScheduledTask | null>(null)
  const { toasts, addToast, dismissToast } = useToasts()

  const loadTasks = useCallback(
    async (currentFilter: ScheduledTaskFilter, silent = false) => {
      if (!silent) setRefreshing(true)
      try {
        const result = await listScheduledTasks(currentFilter)
        setTasks(result)
      } catch (err) {
        addToast({
          type: 'error',
          message:
            err instanceof Error ? err.message : 'Failed to load tasks',
        })
      } finally {
        if (!silent) setRefreshing(false)
        setLoading(false)
      }
    },
    [addToast],
  )

  useEffect(() => {
    void loadTasks(filter)
  }, [filter, loadTasks])

  // Listen for scheduled_task_result SSE events to refresh the list
  // and show a toast when a task fires.
  const handleSseEvent = useCallback(
    (event: SSEEvent) => {
      if (event.type !== 'scheduled_task_result') return
      const eventPayload =
        event.data && typeof event.data === 'object'
          ? (event.data as Record<string, unknown>)
          : null
      const taskName = String(eventPayload?.task_name || 'Scheduled task')
      const taskType = String(eventPayload?.task_type || '')
      const resultText = String(eventPayload?.result_text || '')
      const status = String(eventPayload?.status || 'ok')
      const isError = status === 'error'
      const summary =
        resultText.length > 200 ? resultText.slice(0, 200) + '...' : resultText
      addToast({
        type: isError ? 'error' : 'success',
        message:
          taskType === 'reminder'
            ? `🔔 ${taskName}: ${summary}`
            : `✅ ${taskName}: ${summary}`,
      })
      // Refresh the task list so last_run_at, status, history update live.
      void loadTasks(filter, true)
    },
    [addToast, filter, loadTasks],
  )

  const { connect, disconnect } = useRealtimeStream(handleSseEvent, null)

  useEffect(() => {
    void connect()
    return () => disconnect()
  }, [connect, disconnect])

  const counts = useMemo(() => {
    return {
      total: tasks.length,
    }
  }, [tasks])

  const handlePauseToggle = useCallback(
    async (task: ScheduledTask) => {
      const nextStatus = task.status === 'paused' ? 'active' : 'paused'
      try {
        await updateScheduledTaskStatus(task.id, nextStatus)
        addToast({
          type: 'success',
          message: nextStatus === 'paused' ? 'Task paused' : 'Task resumed',
        })
        void loadTasks(filter, true)
      } catch (err) {
        addToast({
          type: 'error',
          message:
            err instanceof Error ? err.message : 'Failed to update task',
        })
      }
    },
    [addToast, filter, loadTasks],
  )

  const handleDelete = useCallback(async () => {
    if (!pendingDelete) return
    const target = pendingDelete
    setPendingDelete(null)
    try {
      await deleteScheduledTask(target.id)
      addToast({ type: 'success', message: `Task "${target.name}" deleted` })
      void loadTasks(filter, true)
    } catch (err) {
      addToast({
        type: 'error',
        message:
          err instanceof Error ? err.message : 'Failed to delete task',
      })
    }
  }, [addToast, filter, loadTasks, pendingDelete])

  const handleCreated = useCallback(
    (_taskId: string, name: string) => {
      addToast({ type: 'success', message: `Task "${name}" created` })
      void loadTasks(filter, true)
    },
    [addToast, filter, loadTasks],
  )

  return (
    <AdminLayout title="Scheduled Tasks">
      <div className="flex-1 overflow-y-auto p-6">
        {/* Header */}
        <header className="mb-8 space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-1">
              <h1 className="text-2xl font-semibold tracking-tight">
                Scheduled Tasks
              </h1>
              <p className="text-sm text-muted-foreground">
                Automate reminders and background AI work on a schedule.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              <Plus className="h-4 w-4" />
              New Task
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
                    'rounded-md px-3 py-1.5 text-xs font-medium transition-all',
                    filter === f.key
                      ? 'bg-foreground text-background'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-3 text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
              <span className="font-mono tabular-nums">
                {counts.total} {counts.total === 1 ? 'task' : 'tasks'}
              </span>
              <button
                type="button"
                onClick={() => loadTasks(filter)}
                disabled={refreshing}
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
                title="Refresh"
                aria-label="Refresh"
              >
                <RefreshCw
                  className={cn(
                    'h-3.5 w-3.5',
                    refreshing && 'animate-spin',
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
        onError={(message) => addToast({ type: 'error', message })}
      />

      <CreateTaskModal
        open={editTask !== null}
        task={editTask}
        onClose={() => setEditTask(null)}
        onCreated={(_id, name) => {
          addToast({ type: 'success', message: `Task "${name}" updated` })
          void loadTasks(filter, true)
        }}
        onError={(message) => addToast({ type: 'error', message })}
      />

      <TaskDetailsModal
        task={detailsTask}
        onClose={() => setDetailsTask(null)}
      />

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete scheduled task?</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDelete
                ? `"${pendingDelete.name}" will be removed permanently. This cannot be undone.`
                : ''}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={handleDelete}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </AdminLayout>
  )
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
  )
}

interface EmptyStateProps {
  onCreate: () => void
  filter: ScheduledTaskFilter
}

function EmptyState({ onCreate, filter }: EmptyStateProps) {
  const isFiltered = filter !== 'all'
  return (
    <div className="flex min-h-[400px] flex-col items-center justify-center gap-5 rounded-xl border border-dashed border-border py-12 text-center">
      <div className="relative">
        <div className="absolute inset-0 -m-2 rounded-full bg-foreground/5 blur-xl" />
        <CalendarClock className="relative h-12 w-12 text-muted-foreground" />
      </div>
      <div className="space-y-1.5 max-w-sm">
        <h2 className="text-base font-semibold">
          {isFiltered ? `No ${filter} tasks` : 'No scheduled tasks yet'}
        </h2>
        <p className="text-sm text-muted-foreground">
          {isFiltered
            ? 'Try a different filter or create a new task.'
            : 'Create your first task to automate reminders or background AI work.'}
        </p>
      </div>
      <button
        type="button"
        onClick={onCreate}
        className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
      >
        <Plus className="h-4 w-4" />
        {isFiltered ? 'Create new task' : 'Create first task'}
      </button>
    </div>
  )
}
