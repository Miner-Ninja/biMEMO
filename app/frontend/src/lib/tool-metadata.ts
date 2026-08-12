import {
  Activity,
  Brain,
  Calendar,
  Code,
  ExternalLink,
  FilePen,
  FileSearch,
  FileText,
  GitBranch,
  GitCommit,
  Globe,
  Mail,
  Sparkles,
  Terminal,
  Wrench,
} from "lucide-react";
import type { ComponentType } from "react";

import type { TranslationKey } from "@/i18n";

/** Trust zone — green: silent, yellow: notify after, red: confirm before. */
export type Zone = "green" | "yellow" | "red";
export type ToolIcon = ComponentType<{ className?: string }>;

export interface ToolMeta {
  icon: ToolIcon;
  zone: Zone;
  /** Optional i18n key for a friendly label; otherwise the raw tool name shows. */
  labelKey?: TranslationKey;
}

/**
 * Single source of truth for how the UI presents each backend tool: icon, trust
 * zone, and friendly label. Tool NAMES and ZONES mirror the backend
 * (`app/backend/trust/zones.py` + `app/backend/security/policy.py`); keep them in
 * sync. Unknown tools resolve to a wrench + RED via {@link getToolMeta}, matching
 * the backend's fail-closed zone gate (unlisted tool → RED).
 */
const TOOL_META: Record<string, ToolMeta> = {
  // GREEN — silent (backend _KNOWN_GREEN_TOOLS)
  file_read: { icon: FileText, zone: "green" },
  file_search: { icon: FileSearch, zone: "green" },
  memory_recall: { icon: Brain, zone: "green" },
  system_status: { icon: Activity, zone: "green" },
  skill_create: { icon: Sparkles, zone: "green" },
  skill_search: { icon: Sparkles, zone: "green" },
  skill_load: { icon: Sparkles, zone: "green" },
  // YELLOW — notify after (backend trust.yellow_zone_tools)
  file_write: { icon: FilePen, zone: "yellow" },
  web_search: { icon: Globe, zone: "yellow" },
  schedule_task: { icon: Calendar, zone: "yellow" },
  http_fetch: { icon: ExternalLink, zone: "yellow", labelKey: "chat.toolFetchUrl" },
  // RED — confirm before (email_send is explicit; the rest are fail-closed RED)
  email_send: { icon: Mail, zone: "red", labelKey: "chat.toolSendEmail" },
  code_search: { icon: Code, zone: "red" },
  git_status: { icon: GitBranch, zone: "red" },
  git_diff: { icon: GitBranch, zone: "red" },
  git_log: { icon: GitBranch, zone: "red" },
  git_commit: { icon: GitCommit, zone: "red" },
  git_push: { icon: GitBranch, zone: "red" },
  execute_command: { icon: Terminal, zone: "red" },
  execute_command_stream: { icon: Terminal, zone: "red" },
};

const UNKNOWN_TOOL_META: ToolMeta = { icon: Wrench, zone: "red" };

/** Presentation metadata for one tool; unknown → wrench + RED (fail-closed). */
export function getToolMeta(name: string): ToolMeta {
  return TOOL_META[name] ?? UNKNOWN_TOOL_META;
}

/** Trust zone → shadcn Badge variant. */
export const ZONE_BADGE_VARIANT: Record<Zone, "success" | "warning" | "danger"> = {
  green: "success",
  yellow: "warning",
  red: "danger",
};

/** Trust zone → dot background class. */
export const ZONE_DOT_CLASS: Record<Zone, string> = {
  green: "bg-success",
  yellow: "bg-warning",
  red: "bg-destructive",
};
