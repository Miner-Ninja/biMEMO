import { z } from "zod";
import { apiRequest } from "@/lib/bimemo-api";

const scheduledTaskTypes = ["reminder", "agent_task"] as const;
export type ScheduledTaskType = (typeof scheduledTaskTypes)[number];

const scheduledTaskStatuses = ["active", "paused", "completed"] as const;
export type ScheduledTaskStatus = (typeof scheduledTaskStatuses)[number];

const scheduleKinds = ["once", "interval", "cron"] as const;
export type ScheduleKind = (typeof scheduleKinds)[number];

/** One scheduled task row as stored by the backend database. */
export const scheduledTaskSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  task_type: z.enum(scheduledTaskTypes),
  prompt: z.string(),
  schedule_kind: z.enum(scheduleKinds),
  schedule_expr: z.string(),
  next_run_at: z.string(),
  status: z.enum(scheduledTaskStatuses),
  last_run_at: z.string().nullable(),
  // Free-form TEXT column in the backend (no CHECK constraint).
  last_status: z.string().nullable(),
  last_error: z.string().nullable(),
  repeat_limit: z.number().nullable(),
  repeat_count: z.number(),
  allowed_tools: z.string(),
  conversation_id: z.string().nullable(),
  deliver: z.string(),
  timezone: z.string(),
  created_at: z.string(),
});

export type ScheduledTask = z.infer<typeof scheduledTaskSchema>;

/** One entry of the tool catalog projection returned by the orchestrator. */
export const toolCatalogEntrySchema = z.looseObject({
  name: z.string(),
  description: z.string(),
});

export type ToolCatalogEntry = z.infer<typeof toolCatalogEntrySchema>;

export interface CreateScheduledTaskPayload {
  name: string;
  task_type: ScheduledTaskType;
  prompt: string;
  schedule: string;
  allowed_tools: string[];
  repeat_limit: number | null;
  conversation_id: string;
}

/** Body of POST /admin/scheduled-tasks. */
export const createScheduledTaskResponseSchema = z.looseObject({
  task_id: z.string(),
  next_run_at: z.string(),
});

export type CreateScheduledTaskResponse = z.infer<
  typeof createScheduledTaskResponseSchema
>;

export type ScheduledTaskFilter = "all" | ScheduledTaskStatus;

/** Body of GET /admin/scheduled-tasks. */
export const listScheduledTasksResponseSchema = z.looseObject({
  tasks: z.array(scheduledTaskSchema),
});

export async function listScheduledTasks(
  filter: ScheduledTaskFilter = "all"
): Promise<ScheduledTask[]> {
  const result = await apiRequest(
    `/admin/scheduled-tasks?status=${encodeURIComponent(filter)}`,
    {},
    listScheduledTasksResponseSchema
  );
  return result.tasks;
}

export async function createScheduledTask(
  payload: CreateScheduledTaskPayload
): Promise<CreateScheduledTaskResponse> {
  return apiRequest(
    "/admin/scheduled-tasks",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    createScheduledTaskResponseSchema
  );
}

/** Body of PATCH /admin/scheduled-tasks/{id} (shared by both PATCH call sites). */
export const updateScheduledTaskResponseSchema = z.looseObject({
  ok: z.boolean(),
});

export async function updateScheduledTaskStatus(
  taskId: string,
  status: ScheduledTaskStatus
): Promise<void> {
  await apiRequest(
    `/admin/scheduled-tasks/${encodeURIComponent(taskId)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ status }),
    },
    updateScheduledTaskResponseSchema
  );
}

export interface UpdateScheduledTaskPayload {
  name?: string;
  prompt?: string;
  schedule?: string;
  allowed_tools?: string[];
  repeat_limit?: number | null;
}

export async function updateScheduledTaskFields(
  taskId: string,
  payload: UpdateScheduledTaskPayload
): Promise<void> {
  await apiRequest(
    `/admin/scheduled-tasks/${encodeURIComponent(taskId)}`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    },
    updateScheduledTaskResponseSchema
  );
}

export async function deleteScheduledTask(taskId: string): Promise<void> {
  await apiRequest(`/admin/scheduled-tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
}

/** Body of GET /admin/tools. */
export const listToolCatalogResponseSchema = z.looseObject({
  tools: z.array(toolCatalogEntrySchema),
});

export async function listToolCatalog(): Promise<ToolCatalogEntry[]> {
  const result = await apiRequest("/admin/tools", {}, listToolCatalogResponseSchema);
  return result.tools;
}

const taskRunStatuses = ["ok", "error", "timeout", "silent", "skipped"] as const;
export type TaskRunStatus = (typeof taskRunStatuses)[number];

/** One scheduled_task_runs row as stored by the backend database. */
export const scheduledTaskRunSchema = z.looseObject({
  id: z.string(),
  task_id: z.string(),
  started_at: z.string(),
  finished_at: z.string().nullable(),
  status: z.enum(taskRunStatuses),
  result_text: z.string(),
  error_message: z.string(),
  duration_ms: z.number(),
});

export type ScheduledTaskRun = z.infer<typeof scheduledTaskRunSchema>;

/** Body of GET /admin/scheduled-tasks/{id}/runs. */
export const listTaskRunsResponseSchema = z.looseObject({
  runs: z.array(scheduledTaskRunSchema),
});

export async function listTaskRuns(
  taskId: string,
  limit = 20
): Promise<ScheduledTaskRun[]> {
  const result = await apiRequest(
    `/admin/scheduled-tasks/${encodeURIComponent(taskId)}/runs?limit=${limit}`,
    {},
    listTaskRunsResponseSchema
  );
  return result.runs;
}

export function parseAllowedTools(raw: string): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}
