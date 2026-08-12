import { Check, Wrench } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

type Zone = "green" | "yellow" | "red";

interface ToolDef {
  name: string;
  zone: Zone;
}

/** Tools grouped by purpose. Zone dot: green silent, yellow notify, red confirm. */
const TOOL_GROUPS: { label: string; tools: ToolDef[] }[] = [
  {
    label: "Файлы",
    tools: [
      { name: "file_read", zone: "green" },
      { name: "file_search", zone: "green" },
      { name: "file_write", zone: "yellow" },
    ],
  },
  {
    label: "Git / Код",
    tools: [
      { name: "code_search", zone: "green" },
      { name: "git_status", zone: "red" },
      { name: "git_diff", zone: "red" },
      { name: "git_commit", zone: "red" },
      { name: "git_push", zone: "red" },
    ],
  },
  {
    label: "Шелл",
    tools: [{ name: "execute_command", zone: "red" }],
  },
  {
    label: "Веб",
    tools: [
      { name: "web_search", zone: "yellow" },
      { name: "http_fetch", zone: "yellow" },
    ],
  },
  {
    label: "Память",
    tools: [{ name: "memory_recall", zone: "green" }],
  },
  {
    label: "Связь",
    tools: [{ name: "email_send", zone: "red" }],
  },
  {
    label: "Система",
    tools: [
      { name: "system_status", zone: "green" },
      { name: "schedule_task", zone: "yellow" },
    ],
  },
];

const ZONE_DOT: Record<Zone, string> = {
  green: "bg-success",
  yellow: "bg-warning",
  red: "bg-destructive",
};

interface ToolMenuProps {
  selected: string[];
  onToggle: (name: string) => void;
  disabled?: boolean;
}

/**
 * Wrench button that opens a grouped menu of tools the assistant can use. Each
 * tool shows a trust-zone colour dot; selecting a tool toggles it into the
 * chips row in the chat input (the forced sequence for the next message).
 */
export function ToolMenu({ selected, onToggle, disabled }: ToolMenuProps) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={disabled}
          title="Инструменты"
          aria-label="Инструменты"
        >
          <Wrench className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 w-56 overflow-y-auto">
        {TOOL_GROUPS.map((group, index) => (
          <div key={group.label}>
            {index > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="text-xs text-muted-foreground">
              {group.label}
            </DropdownMenuLabel>
            <DropdownMenuGroup>
              {group.tools.map((tool) => (
                <DropdownMenuItem
                  key={tool.name}
                  // Keep the menu open while toggling several tools.
                  onSelect={(event) => {
                    event.preventDefault();
                    onToggle(tool.name);
                  }}
                  className="gap-2"
                >
                  <span className={cn("h-2 w-2 rounded-full", ZONE_DOT[tool.zone])} />
                  <span className="flex-1 font-mono text-xs">{tool.name}</span>
                  {selected.includes(tool.name) && <Check className="h-3.5 w-3.5" />}
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
