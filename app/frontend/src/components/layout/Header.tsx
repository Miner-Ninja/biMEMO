import { Database, Info } from 'lucide-react'
import { BridgeProviderIcon } from '@/components/icons/BridgeProviderIcon'
import { Button } from '@/components/ui/button'
import { ThemeToggle } from './ThemeToggle'
import { cn } from '@/lib/utils'

interface HeaderProps {
  title?: string
  bridgeProvider?: string | null
  onOpenBridgeDetails?: () => void
  className?: string
}

/**
 * Application header with title and actions
 */
function bridgeProviderLabel(provider: string): string {
  return provider.charAt(0).toUpperCase() + provider.slice(1)
}

export function Header({
  title,
  bridgeProvider,
  onOpenBridgeDetails,
  className,
}: HeaderProps) {
  return (
    <header className={cn('flex h-11 items-center justify-between gap-1.5 px-2 sm:h-14 sm:gap-3 sm:px-4', className)}>
      <div className="flex min-w-0 items-center gap-1.5 sm:gap-3">
        <Database className="hidden h-4 w-4 shrink-0 text-primary sm:hidden md:hidden" />
        <div className="flex min-w-0 items-center gap-1 sm:gap-2">
          <h1 className="truncate text-[15px] font-semibold tracking-tight sm:text-lg">
            {title || 'CEO AI Assistant'}
          </h1>
          {bridgeProvider ? (
            <span
              className="inline-flex shrink-0 items-center gap-1 rounded-full border border-primary/20 bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary sm:gap-1.5 sm:px-2 sm:text-xs"
              title={`${bridgeProviderLabel(bridgeProvider)} bridge connected`}
            >
              <BridgeProviderIcon provider={bridgeProvider} className="h-3 w-3" />
              <span className="hidden sm:inline">{bridgeProviderLabel(bridgeProvider)}</span>
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
            </span>
          ) : null}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1 sm:gap-2">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={onOpenBridgeDetails}
          className="h-7.5 w-7.5 sm:h-9 sm:w-9"
          aria-label="Chat bridge details"
        >
          <Info className="h-4 w-4 sm:h-4.5 sm:w-4.5" />
        </Button>
        <ThemeToggle />
      </div>
    </header>
  )
}
