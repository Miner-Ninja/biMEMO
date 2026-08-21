import { Badge } from "@/components/ui/badge";
import { cn } from "@/utils/utils";
import { getToolMeta } from "@/features/chat/model/tool-metadata";
import type { UsedTool } from "@/features/chat/model/chat";

interface CollapsedTool {
  tool: UsedTool;
  count: number;
  totalDurationMs: number | null;
}

/** Collapse consecutive identical executions into a compact count. */
function collapse(tools: UsedTool[]): CollapsedTool[] {
  const out: CollapsedTool[] = [];
  for (const tool of tools) {
    const last = out[out.length - 1];
    if (last && last.tool.name === tool.name && last.tool.success === tool.success) {
      last.count += 1;
      if (tool.duration_ms != null) {
        last.totalDurationMs = (last.totalDurationMs ?? 0) + tool.duration_ms;
      }
    } else {
      out.push({ tool, count: 1, totalDurationMs: tool.duration_ms ?? null });
    }
  }
  return out;
}

/** Format a millisecond duration for the badge tooltip (ms below 1s, s above). */
function formatDuration(durationMs: number): string {
  if (durationMs < 1000) {
    return `${Math.round(durationMs)}ms`;
  }
  return `${(durationMs / 1000).toFixed(1)}s`;
}

interface ToolBadgesProps {
  usedTools: UsedTool[];
  className?: string;
}

/**
 * A row of small badges for the tools a message actually executed, in order.
 * Failed execution is shown struck-through; identical consecutive executions
 * collapse to ×N. Renders nothing when empty.
 */
export function ToolBadges({ usedTools, className }: ToolBadgesProps) {
  if (!usedTools?.length) return null;

  return (
    <div className={cn("flex flex-wrap justify-end gap-1", className)}>
      {collapse(usedTools).map(({ tool, count, totalDurationMs }, index) => {
        const Icon = getToolMeta(tool.name).icon;
        const variant = tool.success ? "secondary" : "danger";
        const durationLabel =
          totalDurationMs != null ? ` · ${formatDuration(totalDurationMs)}` : "";
        const title =
          (tool.success ? tool.name : `${tool.name} (failed)`) + durationLabel;

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
