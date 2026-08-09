import { useTranslation } from "react-i18next";
import { Clock, Cpu, Database, Layers } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { ExtendedUsage } from "@/types/stats";
import { cn } from "@/lib/utils";

interface TokenStatsProps {
  stats: ExtendedUsage;
  className?: string;
}

/**
 * Compact display of token usage, timing, and context stats.
 * Shows: tokens in/out, time, context usage with warning.
 */
export function TokenStats({ stats, className }: TokenStatsProps) {
  const { t } = useTranslation();
  const stages = stats.timing.stages;
  const contextUsage = stats.context;
  const hasContextUsage = Boolean(contextUsage && contextUsage.total > 0);
  const contextPercent = contextUsage?.used_percent ?? 0;
  const isContextWarning = contextPercent >= 80;
  const generationSeconds = stats.timing.generation_ms / 1000;
  const tokensPerSecond =
    generationSeconds > 0 ? stats.completion_tokens / generationSeconds : null;

  // Format time nicely
  const formatTime = (ms: number): string => {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  // Format token count with K suffix for large numbers
  const formatTokens = (count: number | undefined | null): string => {
    if (count == null) return "0";
    if (count >= 1000) return `${(count / 1000).toFixed(1)}K`;
    return count.toString();
  };

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-1.5 text-[10px] leading-none sm:text-xs",
        className
      )}
    >
      <Badge variant="secondary" className="gap-1 whitespace-nowrap tabular-nums">
        <Cpu className="h-3 w-3 shrink-0" />
        <span title={t("chat.inputOutputTokens")} className="whitespace-nowrap">
          {t("chat.tokensInOut", {
            in: formatTokens(stats.prompt_tokens),
            out: formatTokens(stats.completion_tokens),
          })}
        </span>
      </Badge>

      <Badge
        variant="secondary"
        className="gap-1 whitespace-nowrap tabular-nums"
        title={
          stages && stages.length > 0
            ? stages.map((s) => `${s.label}  ${formatTime(s.duration_ms)}`).join("\n")
            : t("chat.promptGenerationTiming", {
                prompt: formatTime(stats.timing.prompt_ms),
                generation: formatTime(stats.timing.generation_ms),
              })
        }
      >
        <Clock className="h-3 w-3 shrink-0" />
        <span className="whitespace-nowrap">{formatTime(stats.timing.total_ms)}</span>
        {stats.iterations > 1 && (
          <span className="whitespace-nowrap text-muted-foreground/70">
            {t("chat.iterations", { count: stats.iterations })}
          </span>
        )}
      </Badge>

      {tokensPerSecond !== null && (
        <Badge
          variant="secondary"
          className="gap-1 whitespace-nowrap tabular-nums"
          title={t("chat.generationSpeed", { value: tokensPerSecond.toFixed(1) })}
        >
          <span className="whitespace-nowrap">
            {t("chat.tokensPerSecond", { value: tokensPerSecond.toFixed(1) })}
          </span>
        </Badge>
      )}

      {hasContextUsage && contextUsage && (
        <Badge
          variant={isContextWarning ? "warning" : "secondary"}
          className="gap-1 whitespace-nowrap tabular-nums"
          title={t("chat.contextTooltip", {
            used: contextUsage.used,
            total: contextUsage.total,
            free: contextUsage.free,
          })}
        >
          <Database className="h-3 w-3 shrink-0" />
          <span className="whitespace-nowrap">
            {t("chat.contextUsage", {
              used: formatTokens(contextUsage.used),
              total: formatTokens(contextUsage.total),
              percent: Math.round(contextPercent),
            })}
          </span>
        </Badge>
      )}

      {stats.memory_zones && (
        <Badge
          variant="secondary"
          className="gap-1 whitespace-nowrap tabular-nums"
          title={t("chat.memoryZonesTooltip", {
            workingUsed: stats.memory_zones.working_memory.used,
            workingBudget: stats.memory_zones.working_memory.budget,
            summaryUsed: stats.memory_zones.conversation_summary.used,
            summaryBudget: stats.memory_zones.conversation_summary.budget,
            recallUsed: stats.memory_zones.recall_results.used,
            recallBudget: stats.memory_zones.recall_results.budget,
          })}
        >
          <Layers className="h-3 w-3 shrink-0" />
          <span className="whitespace-nowrap">
            {t("chat.memoryZonesShort", {
              workingUsed: formatTokens(stats.memory_zones.working_memory.used),
              workingBudget: formatTokens(stats.memory_zones.working_memory.budget),
              summaryUsed: formatTokens(stats.memory_zones.conversation_summary.used),
              summaryBudget: formatTokens(
                stats.memory_zones.conversation_summary.budget
              ),
              recallUsed: formatTokens(stats.memory_zones.recall_results.used),
              recallBudget: formatTokens(stats.memory_zones.recall_results.budget),
            })}
          </span>
        </Badge>
      )}
    </div>
  );
}
