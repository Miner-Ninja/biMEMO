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

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { UsedTool } from "@/types/chat";

/** Tool name → Lucide icon. Unmapped tools fall back to a wrench. */
const TOOL_ICON: Record<string, ComponentType<{ className?: string }>> = {
  file_read: FileText,
  file_search: FileSearch,
  file_write: FilePen,
  code_search: Code,
  web_search: Globe,
  http_fetch: ExternalLink,
  memory_recall: Brain,
  email_send: Mail,
  schedule_task: Calendar,
  system_status: Activity,
  execute_command: Terminal,
  execute_command_stream: Terminal,
  git_status: GitBranch,
  git_diff: GitBranch,
  git_log: GitBranch,
  git_commit: GitCommit,
  git_push: GitBranch,
  skill_create: Sparkles,
  skill_search: Sparkles,
  skill_load: Sparkles,
};

/** Trust-zone value (from get_zone) → Badge variant. */
const ZONE_VARIANT: Record<string, "success" | "warning" | "danger"> = {
  green: "success",
  yellow: "warning",
  red: "danger",
};

interface CollapsedTool {
  tool: UsedTool;
  count: number;
}

/** Collapse consecutive identical executions (same name, zone, success) into ×N. */
function collapse(tools: UsedTool[]): CollapsedTool[] {
  const out: CollapsedTool[] = [];
  for (const tool of tools) {
    const last = out[out.length - 1];
    if (
      last &&
      last.tool.name === tool.name &&
      last.tool.zone === tool.zone &&
      last.tool.success === tool.success
    ) {
      last.count += 1;
    } else {
      out.push({ tool, count: 1 });
    }
  }
  return out;
}

interface ToolBadgesProps {
  usedTools: UsedTool[];
  className?: string;
}

/**
 * A row of small badges for the tools a message actually executed, in order.
 * Zone drives the colour; a failed execution is shown struck-through; identical
 * consecutive executions collapse to ×N. Renders nothing when empty.
 */
export function ToolBadges({ usedTools, className }: ToolBadgesProps) {
  if (!usedTools?.length) return null;

  return (
    <div className={cn("flex flex-wrap justify-end gap-1", className)}>
      {collapse(usedTools).map(({ tool, count }, index) => {
        const Icon = TOOL_ICON[tool.name] ?? Wrench;
        const variant = tool.success
          ? (ZONE_VARIANT[tool.zone] ?? "secondary")
          : "danger";
        const title = tool.success ? tool.name : `${tool.name} (failed)`;

        return (
          <Badge
            // Position-stable key; names may repeat across the row.
            key={`${tool.name}-${index}`}
            variant={variant}
            title={title}
            className={cn("gap-1 px-1.5 py-0", !tool.success && "line-through")}
          >
            <Icon className="h-3 w-3" />
            <span>{tool.name}</span>
            {count > 1 && <span className="opacity-70">×{count}</span>}
          </Badge>
        );
      })}
    </div>
  );
}
