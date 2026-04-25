/**
 * Inline editor for list-of-string settings (allowlists, regex
 * patterns, hostnames). Renders the current items as removable
 * Badge chips and exposes an Input + add Button to append new
 * entries. Empty entries are ignored.
 */

import { useState, type FormEvent } from "react"
import { Plus, X } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

interface ListEditorProps {
  values: string[]
  placeholder?: string
  onChange: (next: string[]) => void
}

export function ListEditor({ values, placeholder, onChange }: ListEditorProps) {
  const [draft, setDraft] = useState("")

  const handleAdd = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmed = draft.trim()
    if (!trimmed) return
    if (values.includes(trimmed)) {
      setDraft("")
      return
    }
    onChange([...values, trimmed])
    setDraft("")
  }

  const handleRemove = (item: string) => {
    onChange(values.filter((entry) => entry !== item))
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5">
        {values.length === 0 ? (
          <span className="text-xs text-muted-foreground">
            Список пуст
          </span>
        ) : (
          values.map((item) => (
            <Badge
              key={item}
              variant="secondary"
              className="flex items-center gap-1 font-mono text-xs"
            >
              <span className="break-all">{item}</span>
              <button
                type="button"
                onClick={() => handleRemove(item)}
                aria-label={`Удалить ${item}`}
                className="-mr-1 inline-flex size-4 items-center justify-center rounded-sm hover:bg-muted-foreground/20"
              >
                <X className="size-3" />
              </button>
            </Badge>
          ))
        )}
      </div>
      <form onSubmit={handleAdd} className="flex items-center gap-2">
        <Input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={placeholder ?? "Добавить запись"}
          className="h-8"
        />
        <Button
          type="submit"
          variant="outline"
          size="sm"
          disabled={!draft.trim()}
        >
          <Plus />
          Добавить
        </Button>
      </form>
    </div>
  )
}
