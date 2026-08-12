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
import { getToolMeta, ZONE_DOT_CLASS } from "@/lib/tool-metadata";
import type { TranslationKey } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * Tools grouped by purpose. Tool names + their trust zones come from the shared
 * tool-metadata table (which mirrors the backend), so the zone dot cannot drift.
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
 * tool shows a trust-zone colour dot; selecting a tool toggles it into the
 * chips row in the chat input (the forced sequence for the next message).
 */
export function ToolMenu({ selected, onToggle, disabled }: ToolMenuProps) {
  const { t } = useTranslation();

  return (
    <DropdownMenu>
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
        {TOOL_GROUPS.map((group, index) => (
          <div key={group.labelKey}>
            {index > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="text-xs text-muted-foreground">
              {t(group.labelKey)}
            </DropdownMenuLabel>
            <DropdownMenuGroup>
              {group.tools.map((name) => (
                <DropdownMenuItem
                  key={name}
                  // Keep the menu open while toggling several tools.
                  onSelect={(event) => {
                    event.preventDefault();
                    onToggle(name);
                  }}
                  className="gap-2"
                >
                  <span
                    className={cn(
                      "h-2 w-2 rounded-full",
                      ZONE_DOT_CLASS[getToolMeta(name).zone]
                    )}
                  />
                  <span className="flex-1 font-mono text-xs">{name}</span>
                  {selected.includes(name) && <Check className="h-3.5 w-3.5" />}
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
