import { memo } from 'react'
import { Check, AlertCircle } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { LiveTimer } from './LiveTimer'
import { cn } from '@/lib/utils'
import type { ProcessingStage } from '@/types/chat'

interface ProcessingStagesProps {
  stages: ProcessingStage[]
  className?: string
}

const BouncingDots = () => (
  <div className="flex items-center gap-1">
    <span className="h-2 w-2 animate-bounce rounded-full bg-primary [animation-delay:-0.3s]" />
    <span className="h-2 w-2 animate-bounce rounded-full bg-primary [animation-delay:-0.15s]" />
    <span className="h-2 w-2 animate-bounce rounded-full bg-primary" />
  </div>
)

const StatusIcon = ({ status }: { status: ProcessingStage['status'] }) => {
  switch (status) {
    case 'active':
      return <BouncingDots />
    case 'completed':
      return <Check className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
    case 'error':
      return <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
  }
}

/**
 * Shows only the current (last) processing stage.
 * Animates transition when stage changes via key-based remount.
 */
export const ProcessingStages = memo(function ProcessingStages({ stages, className }: ProcessingStagesProps) {
  if (stages.length === 0) return null

  const current = stages[stages.length - 1]
  const statusBadgeVariant = current.status === 'completed'
    ? 'secondary'
    : current.status === 'error'
      ? 'danger'
      : 'info'

  return (
    <div className={cn('px-2 py-2 sm:px-4 sm:py-3', className)}>
      <Card className="border-border/70 bg-card/80 shadow-none">
        <CardContent className="flex items-center gap-3 p-3 sm:p-4">
          <div
            key={current.id}
            className={cn(
              'flex min-w-0 flex-1 items-center gap-3 animate-in fade-in slide-in-from-bottom-2 duration-300',
              current.status === 'completed' && 'text-muted-foreground',
              current.status === 'error' && 'text-destructive',
            )}
          >
            <StatusIcon status={current.status} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm">{current.label}</div>
            </div>
            <Badge variant={statusBadgeVariant}>
              {current.status}
            </Badge>
            <LiveTimer
              startedAt={current.startedAt}
              frozenMs={current.frozenDurationMs}
            />
          </div>
        </CardContent>
      </Card>
    </div>
  )
})
