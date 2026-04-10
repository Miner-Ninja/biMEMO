import { apiRequest } from '@/lib/bimemo-api'

export type ScheduledTaskType = 'reminder' | 'agent_task'
export type ScheduledTaskStatus = 'active' | 'paused' | 'completed'
export type ScheduleKind = 'once' | 'interval' | 'cron'
export type TrustZone = 'green' | 'yellow' | 'red'

export interface ScheduledTask {
  id: string
  name: string
  task_type: ScheduledTaskType
  prompt: string
  schedule_kind: ScheduleKind
  schedule_expr: string
  next_run_at: string
  status: ScheduledTaskStatus
  last_run_at: string | null
  last_status: 'ok' | 'error' | null
  last_error: string | null
  repeat_limit: number | null
  repeat_count: number
  allowed_tools: string
  conversation_id: string | null
  timezone: string
  created_at: string
}

export interface ToolCatalogEntry {
  name: string
  description: string
  zone: TrustZone
}

export interface CreateScheduledTaskPayload {
  name: string
  task_type: ScheduledTaskType
  prompt: string
  schedule: string
  allowed_tools: string[]
  repeat_limit: number | null
  conversation_id: string
}

export interface CreateScheduledTaskResponse {
  task_id: string
  next_run_at: string
}

export type ScheduledTaskFilter = 'all' | ScheduledTaskStatus

export async function listScheduledTasks(
  filter: ScheduledTaskFilter = 'all',
): Promise<ScheduledTask[]> {
  const result = await apiRequest<{ tasks: ScheduledTask[] }>(
    `/admin/scheduled-tasks?status=${encodeURIComponent(filter)}`,
  )
  return result.tasks ?? []
}

export async function createScheduledTask(
  payload: CreateScheduledTaskPayload,
): Promise<CreateScheduledTaskResponse> {
  return apiRequest<CreateScheduledTaskResponse>('/admin/scheduled-tasks', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateScheduledTaskStatus(
  taskId: string,
  status: ScheduledTaskStatus,
): Promise<void> {
  await apiRequest(`/admin/scheduled-tasks/${encodeURIComponent(taskId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export interface UpdateScheduledTaskPayload {
  name?: string
  prompt?: string
  schedule?: string
  allowed_tools?: string[]
  repeat_limit?: number | null
}

export async function updateScheduledTaskFields(
  taskId: string,
  payload: UpdateScheduledTaskPayload,
): Promise<void> {
  await apiRequest(`/admin/scheduled-tasks/${encodeURIComponent(taskId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteScheduledTask(taskId: string): Promise<void> {
  await apiRequest(`/admin/scheduled-tasks/${encodeURIComponent(taskId)}`, {
    method: 'DELETE',
  })
}

export async function listToolCatalog(): Promise<ToolCatalogEntry[]> {
  const result = await apiRequest<{ tools: ToolCatalogEntry[] }>('/admin/tools')
  return result.tools ?? []
}

export type TaskRunStatus = 'ok' | 'error' | 'timeout' | 'silent' | 'skipped'

export interface ScheduledTaskRun {
  id: string
  task_id: string
  started_at: string
  finished_at: string | null
  status: TaskRunStatus
  result_text: string
  error_message: string
  duration_ms: number
}

export async function listTaskRuns(
  taskId: string,
  limit = 20,
): Promise<ScheduledTaskRun[]> {
  const result = await apiRequest<{ runs: ScheduledTaskRun[] }>(
    `/admin/scheduled-tasks/${encodeURIComponent(taskId)}/runs?limit=${limit}`,
  )
  return result.runs ?? []
}

export function parseAllowedTools(raw: string): string[] {
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.map(String) : []
  } catch {
    return []
  }
}
