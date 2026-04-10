import { useEffect, useRef } from 'react'
import { FileText } from 'lucide-react'
import { cn } from '@/lib/utils'

interface FileMentionProps {
  files: string[]
  selectedIndex: number
  onSelect: (filename: string) => void
  onHover: (index: number) => void
}

/**
 * @mention autocomplete popup for file references
 */
export function FileMention({ files, selectedIndex, onSelect, onHover }: FileMentionProps) {
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([])

  useEffect(() => {
    itemRefs.current[selectedIndex]?.scrollIntoView({ block: 'nearest' })
  }, [selectedIndex])

  if (files.length === 0) {
    return (
      <div className="rounded-md border border-border bg-popover p-2 shadow-md">
        <span className="text-sm text-muted-foreground">No matching files</span>
      </div>
    )
  }

  return (
    <div className="rounded-md border border-border bg-popover p-1 shadow-md max-h-48 overflow-y-auto">
      {files.map((filename, index) => (
        <button
          key={filename}
          ref={(el) => { itemRefs.current[index] = el }}
          className={cn(
            'flex w-full items-center gap-2 rounded-sm px-3 py-1.5 text-left text-sm',
            index === selectedIndex
              ? 'bg-accent text-accent-foreground'
              : 'text-popover-foreground hover:bg-accent/50'
          )}
          onMouseDown={(e) => { e.preventDefault(); onSelect(filename) }}
          onMouseEnter={() => onHover(index)}
        >
          <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="truncate">{filename}</span>
        </button>
      ))}
    </div>
  )
}
