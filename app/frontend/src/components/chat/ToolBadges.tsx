import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { getToolMeta } from "@/lib/tool-metadata";
import type { UsedTool } from "@/types/chat";

interface CollapsedTool {
  tool: UsedTool;
  count: number;
}

/** Collapse consecutive identical executions into a compact count. */
function collapse(tools: UsedTool[]): CollapsedTool[] {
  const out: CollapsedTool[] = [];
  for (const tool of tools) {
    const last = out[out.length - 1];
    if (last && last.tool.name === tool.name && last.tool.success === tool.success) {
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
 * Failed execution is shown struck-through; identical consecutive executions
 * collapse to ×N. Renders nothing when empty.
 */
export function ToolBadges({ usedTools, className }: ToolBadgesProps) {
  if (!usedTools?.length) return null;

  return (
    <div className={cn("flex flex-wrap justify-end gap-1", className)}>
      {collapse(usedTools).map(({ tool, count }, index) => {
        const Icon = getToolMeta(tool.name).icon;
        const variant = tool.success ? "secondary" : "danger";
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
