import { Clock, Cpu, Database, Layers } from 'lucide-react'
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
        'flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] leading-none text-muted-foreground sm:gap-x-3 sm:text-xs',
        className
      )}
    >
      {/* Tokens */}
      <div className="inline-flex items-center gap-1 whitespace-nowrap tabular-nums" title="Input / Output tokens">
        <Cpu className="h-3 w-3 shrink-0" />
        <span className="whitespace-nowrap">
          {formatTokens(stats.prompt_tokens)} in / {formatTokens(stats.completion_tokens)} out
        </span>
      </div>

      {/* Time */}
      <div
        className="inline-flex items-center gap-1 whitespace-nowrap tabular-nums"
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
      </div>

      {/* Generation speed */}
      {tokensPerSecond !== null && (
        <div
          className="inline-flex items-center gap-1 whitespace-nowrap tabular-nums"
          title={`Generation speed: ${tokensPerSecond.toFixed(1)} tokens per second`}
        >
          <span className="whitespace-nowrap">{tokensPerSecond.toFixed(1)} tok/s</span>
        </div>
      )}

      {/* Context */}
      {hasContextUsage && contextUsage && (
        <div
          className={cn(
            'inline-flex items-center gap-1 whitespace-nowrap tabular-nums',
            isContextWarning && 'text-orange-500 dark:text-orange-400'
          )}
          title={`Context: ${contextUsage.used} / ${contextUsage.total} tokens (${contextUsage.free} free)`}
        >
          <Database className="h-3 w-3 shrink-0" />
          <span className="whitespace-nowrap">
            {formatTokens(contextUsage.used)} / {formatTokens(contextUsage.total)} ctx ({Math.round(contextPercent)}%)
          </span>
        </div>
      )}

      {/* Memory zones */}
      {stats.memory_zones && (
        <div
          className="inline-flex items-center gap-1 whitespace-nowrap tabular-nums"
          title={`Working: ${stats.memory_zones.working_memory.used}/${stats.memory_zones.working_memory.budget}, Summary: ${stats.memory_zones.conversation_summary.used}/${stats.memory_zones.conversation_summary.budget}, Recall: ${stats.memory_zones.recall_results.used}/${stats.memory_zones.recall_results.budget}`}
        >
          <Layers className="h-3 w-3 shrink-0" />
          <span className="whitespace-nowrap">
            W:{formatTokens(stats.memory_zones.working_memory.used)}/{formatTokens(stats.memory_zones.working_memory.budget)}{' '}
            S:{formatTokens(stats.memory_zones.conversation_summary.used)}/{formatTokens(stats.memory_zones.conversation_summary.budget)}{' '}
            R:{formatTokens(stats.memory_zones.recall_results.used)}/{formatTokens(stats.memory_zones.recall_results.budget)}
          </span>
        </div>
      )}

    </div>
  )
}
