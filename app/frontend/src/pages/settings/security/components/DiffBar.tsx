/**
 * Banner that summarises unsaved local edits against the active
 * profile. Hidden when the draft matches the baseline. Built-in
 * profiles disable the "Overwrite" action — users must
 * Save-As-New to keep changes.
 */

import { useState } from "react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { ChevronDown, ChevronRight } from "lucide-react"
import type { DirtyChange } from "../hooks/useDirtySettings"
import type { SecuritySetting } from "@/api/security"
import { PAGE_COPY } from "../i18n/ru"

interface DiffBarProps {
  changes: DirtyChange[]
  activeProfileLabel: string
  isBuiltinActive: boolean
  catalogByKey: Record<string, SecuritySetting>
  onSaveAsNew: () => void
  onDiscard: () => void
  onOverwrite: () => void
}

export function DiffBar({
  changes,
  activeProfileLabel,
  isBuiltinActive,
  catalogByKey,
  onSaveAsNew,
  onDiscard,
  onOverwrite,
}: DiffBarProps) {
  const [expanded, setExpanded] = useState(false)
  if (changes.length === 0) return null

  return (
    <Alert>
      <AlertTitle className="flex items-center gap-2">
        {PAGE_COPY.unsavedHeading} «{activeProfileLabel}»: {changes.length}
      </AlertTitle>
      <AlertDescription className="flex flex-col gap-2 pt-2">
        <Collapsible open={expanded} onOpenChange={setExpanded}>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="w-fit">
              {expanded ? (
                <ChevronDown className="size-4" />
              ) : (
                <ChevronRight className="size-4" />
              )}
              Показать изменения
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul className="flex flex-col gap-1 pl-2 pt-1">
              {changes.map((change) => {
                const setting = catalogByKey[change.key]
                const label = setting?.label ?? change.key
                return (
                  <li
                    key={change.key}
                    className="text-xs leading-snug font-mono"
                  >
                    {label}: {formatValue(change.before)} →{" "}
                    {formatValue(change.after)}
                  </li>
                )
              })}
            </ul>
          </CollapsibleContent>
        </Collapsible>
        <div className="flex flex-wrap gap-2 pt-1">
          <Button size="sm" onClick={onSaveAsNew}>
            {PAGE_COPY.saveAsNew}
          </Button>
          <Button size="sm" variant="outline" onClick={onDiscard}>
            {PAGE_COPY.discard}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onOverwrite}
            disabled={isBuiltinActive}
            title={isBuiltinActive ? PAGE_COPY.builtInLockHint : undefined}
          >
            {PAGE_COPY.overwrite}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  )
}

function formatValue(value: unknown): string {
  if (typeof value === "boolean") return value ? "ВКЛ" : "ВЫКЛ"
  if (Array.isArray(value)) return `[${value.length} items]`
  if (value === null || value === undefined) return "—"
  return String(value)
}
