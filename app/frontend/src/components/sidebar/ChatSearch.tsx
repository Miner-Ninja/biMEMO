import { Search, X } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { getSidebarSearchInputClassName } from './sidebar-chrome'

interface ChatSearchProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
}

/**
 * Search input for filtering sidebar content.
 */
export function ChatSearch({ value, onChange, placeholder = 'Search...' }: ChatSearchProps) {
  return (
    <div className="relative">
      <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-sidebar-foreground/45" />
      <Input
        type="text"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={getSidebarSearchInputClassName()}
      />
      {value && (
        <Button
          variant="ghost"
          size="icon"
          className="absolute right-2 top-1/2 h-6 w-6 -translate-y-1/2 text-sidebar-foreground/55 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
          onClick={() => onChange('')}
          aria-label="Clear search"
        >
          <X className="h-3 w-3" />
        </Button>
      )}
    </div>
  )
}
