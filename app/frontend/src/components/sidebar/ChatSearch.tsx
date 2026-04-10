import { Search, X } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

interface ChatSearchProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
}

/**
 * Search input for filtering chats or files
 */
export function ChatSearch({ value, onChange, placeholder = 'Search...' }: ChatSearchProps) {
  return (
    <div className="relative px-2">
      <Search className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        type="text"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 pl-8 pr-8 text-sm bg-background border-input"
      />
      {value && (
        <Button
          variant="ghost"
          size="icon"
          className="absolute right-3 top-1/2 h-5 w-5 -translate-y-1/2"
          onClick={() => onChange('')}
          aria-label="Clear search"
        >
          <X className="h-3 w-3" />
        </Button>
      )}
    </div>
  )
}
