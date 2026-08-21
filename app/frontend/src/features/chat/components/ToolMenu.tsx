import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
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
import type { TranslationKey } from "@/i18n";
import {
  listToolCatalog,
  type ToolCatalogEntry,
} from "@/features/scheduler/api/scheduled-tasks";

/**
 * Presentation-only grouping for familiar tool selection.
 */
const TOOL_GROUPS: { labelKey: TranslationKey; tools: string[] }[] = [
  {
    labelKey: "chat.toolGroupFiles",
    tools: ["file_read", "file_search", "file_write"],
  },
  {
    labelKey: "chat.toolGroupGitCode",
    tools: ["code_search", "git_status", "git_diff", "git_commit", "git_push"],
  },
  { labelKey: "chat.toolGroupShell", tools: ["execute_command"] },
  { labelKey: "chat.toolGroupWeb", tools: ["web_search", "http_fetch"] },
  { labelKey: "chat.toolGroupMemory", tools: ["memory_recall"] },
  { labelKey: "chat.toolGroupComms", tools: ["email_send"] },
  { labelKey: "chat.toolGroupSystem", tools: ["system_status", "schedule_task"] },
];

interface ToolMenuProps {
  selected: string[];
  onToggle: (name: string) => void;
  disabled?: boolean;
}

/**
 * Wrench button that opens a grouped menu of tools the assistant can use. Each
 * Selecting a tool toggles it into the chips row for the next message.
 */
export function ToolMenu({ selected, onToggle, disabled }: ToolMenuProps) {
  const { t } = useTranslation();
  const [catalog, setCatalog] = useState<ToolCatalogEntry[]>([]);
  const [catalogStatus, setCatalogStatus] = useState<"idle" | "loading" | "loaded">(
    "idle"
  );

  const loadCatalog = () => {
    if (catalogStatus !== "idle") return;
    setCatalogStatus("loading");
    void listToolCatalog()
      .then(setCatalog)
      .catch(() => setCatalog([]))
      .finally(() => setCatalogStatus("loaded"));
  };

  const groups = useMemo(() => {
    const byName = new Map(catalog.map((tool) => [tool.name, tool]));
    const knownNames = new Set(TOOL_GROUPS.flatMap((group) => group.tools));
    const known = TOOL_GROUPS.map((group) => ({
      ...group,
      tools: group.tools.flatMap((name) => {
        const tool = byName.get(name);
        return tool ? [tool] : [];
      }),
    })).filter((group) => group.tools.length > 0);
    const remaining = catalog.filter((tool) => !knownNames.has(tool.name));
    return remaining.length > 0
      ? [
          ...known,
          { labelKey: "chat.toolGroupOther" as TranslationKey, tools: remaining },
        ]
      : known;
  }, [catalog]);

  return (
    <DropdownMenu onOpenChange={(open) => open && loadCatalog()}>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={disabled}
          title={t("chat.tools")}
          aria-label={t("chat.tools")}
        >
          <Wrench className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 w-56 overflow-y-auto">
        {groups.map((group, index) => (
          <div key={group.labelKey}>
            {index > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="text-xs text-muted-foreground">
              {t(group.labelKey)}
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
                  <span className="flex-1 font-mono text-xs" title={tool.description}>
                    {tool.name}
                  </span>
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
