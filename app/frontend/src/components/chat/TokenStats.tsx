import { Clock, Cpu, Database, Layers } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import type { ExtendedUsage } from '@/types/stats'
import { cn } from '@/lib/utils'

interface TokenStatsProps {
  stats: ExtendedUsage
  stageTimes?: { label: string; durationMs: number }[]
  className?: string
}

/**
 * Compact display of token usage, timing, and context stats.
 * Shows: tokens in/out, time, context usage with warning.
 */
export function TokenStats({ stats, stageTimes, className }: TokenStatsProps) {
  const contextUsage = stats.context
  const hasContextUsage = Boolean(contextUsage && contextUsage.total > 0)
  const contextPercent = contextUsage?.used_percent ?? 0
  const isContextWarning = contextPercent >= 80
  const generationSeconds = stats.timing.generation_ms / 1000
  const tokensPerSecond =
    generationSeconds > 0
      ? stats.completion_tokens / generationSeconds
      : null

  // Format time nicely
  const formatTime = (ms: number): string => {
    if (ms < 1000) return `${Math.round(ms)}ms`
    return `${(ms / 1000).toFixed(1)}s`
  }

  // Format token count with K suffix for large numbers
  const formatTokens = (count: number | undefined | null): string => {
    if (count == null) return '0'
    if (count >= 1000) return `${(count / 1000).toFixed(1)}K`
    return count.toString()
  }

  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-1.5 text-[10px] leading-none sm:text-xs',
        className
      )}
    >
      <Badge variant="secondary" className="gap-1 whitespace-nowrap tabular-nums">
        <Cpu className="h-3 w-3 shrink-0" />
        <span title="Input / Output tokens" className="whitespace-nowrap">
          {formatTokens(stats.prompt_tokens)} in / {formatTokens(stats.completion_tokens)} out
        </span>
      </Badge>

      <Badge
        variant="secondary"
        className="gap-1 whitespace-nowrap tabular-nums"
        title={
          stageTimes && stageTimes.length > 0
            ? stageTimes.map((s) => `${s.label.replace('...', '')}  ${formatTime(s.durationMs)}`).join('\n')
            : `Prompt: ${formatTime(stats.timing.prompt_ms)}, Generation: ${formatTime(stats.timing.generation_ms)}`
        }
      >
        <Clock className="h-3 w-3 shrink-0" />
        <span className="whitespace-nowrap">{formatTime(stats.timing.total_ms)}</span>
        {stats.iterations > 1 && (
          <span className="whitespace-nowrap text-muted-foreground/70">({stats.iterations} iter)</span>
        )}
      </Badge>

      {tokensPerSecond !== null && (
        <Badge
          variant="secondary"
          className="gap-1 whitespace-nowrap tabular-nums"
          title={`Generation speed: ${tokensPerSecond.toFixed(1)} tokens per second`}
        >
          <span className="whitespace-nowrap">{tokensPerSecond.toFixed(1)} tok/s</span>
        </Badge>
      )}

      {hasContextUsage && contextUsage && (
        <Badge
          variant={isContextWarning ? 'warning' : 'secondary'}
          className="gap-1 whitespace-nowrap tabular-nums"
          title={`Context: ${contextUsage.used} / ${contextUsage.total} tokens (${contextUsage.free} free)`}
        >
          <Database className="h-3 w-3 shrink-0" />
          <span className="whitespace-nowrap">
            {formatTokens(contextUsage.used)} / {formatTokens(contextUsage.total)} ctx ({Math.round(contextPercent)}%)
          </span>
        </Badge>
      )}

      {stats.memory_zones && (
        <Badge
          variant="secondary"
          className="gap-1 whitespace-nowrap tabular-nums"
          title={`Working: ${stats.memory_zones.working_memory.used}/${stats.memory_zones.working_memory.budget}, Summary: ${stats.memory_zones.conversation_summary.used}/${stats.memory_zones.conversation_summary.budget}, Recall: ${stats.memory_zones.recall_results.used}/${stats.memory_zones.recall_results.budget}`}
        >
          <Layers className="h-3 w-3 shrink-0" />
          <span className="whitespace-nowrap">
            W:{formatTokens(stats.memory_zones.working_memory.used)}/{formatTokens(stats.memory_zones.working_memory.budget)}{' '}
            S:{formatTokens(stats.memory_zones.conversation_summary.used)}/{formatTokens(stats.memory_zones.conversation_summary.budget)}{' '}
            R:{formatTokens(stats.memory_zones.recall_results.used)}/{formatTokens(stats.memory_zones.recall_results.budget)}
          </span>
        </Badge>
      )}
    </div>
  )
}
