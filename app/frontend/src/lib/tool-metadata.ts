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

export type ToolIcon = ComponentType<{ className?: string }>;

export interface ToolMeta {
  icon: ToolIcon;
  /** Optional i18n key for a friendly label; otherwise the raw tool name shows. */
  labelKey?: TranslationKey;
}

/**
 * Presentation-only icon and friendly label metadata. Permission and routing
 * are intentionally absent; the backend runtime registry owns them.
 */
const TOOL_META: Record<string, ToolMeta> = {
  file_read: { icon: FileText },
  file_search: { icon: FileSearch },
  memory_recall: { icon: Brain },
  system_status: { icon: Activity },
  skill_create: { icon: Sparkles },
  skill_search: { icon: Sparkles },
  skill_load: { icon: Sparkles },
  file_write: { icon: FilePen },
  web_search: { icon: Globe },
  schedule_task: { icon: Calendar },
  http_fetch: { icon: ExternalLink, labelKey: "chat.toolFetchUrl" },
  email_send: { icon: Mail, labelKey: "chat.toolSendEmail" },
  code_search: { icon: Code },
  git_status: { icon: GitBranch },
  git_diff: { icon: GitBranch },
  git_log: { icon: GitBranch },
  git_commit: { icon: GitCommit },
  git_push: { icon: GitBranch },
  execute_command: { icon: Terminal },
  execute_command_stream: { icon: Terminal },
};

const UNKNOWN_TOOL_META: ToolMeta = { icon: Wrench };

/** Presentation metadata for one tool; unknown → wrench + RED (fail-closed). */
export function getToolMeta(name: string): ToolMeta {
  return TOOL_META[name] ?? UNKNOWN_TOOL_META;
}
