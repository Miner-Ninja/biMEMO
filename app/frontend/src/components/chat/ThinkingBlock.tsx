import { useState } from 'react'
import { Brain, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ThinkingBlockProps {
  thinking: string
  className?: string
}

/**
 * Collapsible block displaying LLM thinking/reasoning process.
 * Shows the internal reasoning that led to the response.
 */
export function ThinkingBlock({ thinking, className }: ThinkingBlockProps) {
  const [isOpen, setIsOpen] = useState(true)

  if (!thinking) return null

  return (
    <div className={cn('rounded border border-sidebar-border bg-sidebar p-3', className)}>
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        className="flex w-full items-center gap-2 text-left text-sm text-muted-foreground"
      >
        <ChevronRight
          className={cn('h-4 w-4 transition-transform', isOpen && 'rotate-90')}
        />
        <Brain className="h-4 w-4" />
        <span className="font-medium">Thinking</span>
        <span className="text-xs opacity-70">
          ({thinking.length} chars)
        </span>
      </button>
      {isOpen && (
        <pre className="mt-2 whitespace-pre-wrap text-sm text-muted-foreground/85 font-mono leading-relaxed">
          {thinking}
        </pre>
      )}
    </div>
  )
}
