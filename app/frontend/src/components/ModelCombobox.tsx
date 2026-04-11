import { useEffect, useRef, useState } from 'react'
import { Input } from '@/components/ui/input'

interface ModelComboboxProps {
  value: string
  onChange: (value: string) => void
  models: string[]
  loading: boolean
  placeholder: string
}

const MAX_ITEMS = 50

export function ModelCombobox({ value, onChange, models, loading, placeholder }: ModelComboboxProps) {
  const [open, setOpen] = useState(false)
  const [highlighted, setHighlighted] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const filtered = models
    .filter((m) => m.toLowerCase().includes(value.toLowerCase()))
    .slice(0, MAX_ITEMS)

  // Close on outside click
  useEffect(() => {
    function handleMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
        setHighlighted(-1)
      }
    }
    document.addEventListener('mousedown', handleMouseDown)
    return () => document.removeEventListener('mousedown', handleMouseDown)
  }, [])

  // Scroll highlighted item into view
  useEffect(() => {
    if (highlighted >= 0 && listRef.current) {
      const item = listRef.current.children[highlighted] as HTMLElement | undefined
      item?.scrollIntoView({ block: 'nearest' })
    }
  }, [highlighted])

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      setOpen(true)
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlighted((h) => Math.min(h + 1, filtered.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlighted((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter' && highlighted >= 0) {
      e.preventDefault()
      onChange(filtered[highlighted])
      setOpen(false)
      setHighlighted(-1)
    } else if (e.key === 'Escape') {
      setOpen(false)
      setHighlighted(-1)
    }
  }

  const showDropdown = open && models.length > 0

  return (
    <div ref={containerRef} className="relative">
      <div className="relative">
        <Input
          value={value}
          onChange={(e) => {
            onChange(e.target.value)
            setOpen(true)
            setHighlighted(-1)
          }}
          onFocus={() => {
            if (models.length > 0) {
              setOpen(true)
              setHighlighted(-1)
            }
          }}
          onKeyDown={handleKeyDown}
          placeholder={loading ? 'Loading models…' : placeholder}
          disabled={loading}
          autoComplete="off"
        />
        {loading && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground text-xs animate-pulse">
            ···
          </span>
        )}
      </div>

      {showDropdown && (
        <ul
          ref={listRef}
          className="absolute z-50 mt-1 w-full max-h-60 overflow-auto rounded-md border border-border bg-background shadow-md text-sm"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-muted-foreground">No models found</li>
          ) : (
            filtered.map((model, idx) => (
              <li
                key={model}
                className={`cursor-pointer px-3 py-1.5 ${
                  idx === highlighted ? 'bg-accent text-accent-foreground' : 'hover:bg-accent hover:text-accent-foreground'
                }`}
                onMouseDown={(e) => {
                  e.preventDefault() // prevent input blur before click registers
                  onChange(model)
                  setOpen(false)
                  setHighlighted(-1)
                }}
                onMouseEnter={() => setHighlighted(idx)}
              >
                {model}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}
