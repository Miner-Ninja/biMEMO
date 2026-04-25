import { useCallback, useMemo, useState } from "react"
import type { SecurityProfile, SecuritySettingValue } from "@/api/security"

export interface DirtyChange {
  key: string
  before: SecuritySettingValue
  after: SecuritySettingValue
}

export interface UseDirtySettingsResult {
  draft: Record<string, SecuritySettingValue>
  changes: DirtyChange[]
  isDirty: boolean
  setValue: (key: string, value: SecuritySettingValue) => void
  reset: () => void
}

export function useDirtySettings(
  baseline: SecurityProfile | null,
): UseDirtySettingsResult {
  const [draft, setDraft] = useState<Record<string, SecuritySettingValue>>(
    () => baselineSettings(baseline),
  )
  const [baselineId, setBaselineId] = useState<string | null>(
    baseline?.id ?? null,
  )

  // Reset the draft during render whenever the baseline profile id
  // changes — pending edits belong to a specific profile and must
  // not leak across an activation.
  if (baseline?.id !== baselineId) {
    setBaselineId(baseline?.id ?? null)
    setDraft(baselineSettings(baseline))
  }

  const setValue = useCallback(
    (key: string, value: SecuritySettingValue) => {
      setDraft((prev) => ({ ...prev, [key]: value }))
    },
    [],
  )

  const reset = useCallback(() => {
    setDraft(baselineSettings(baseline))
  }, [baseline])

  const changes = useMemo<DirtyChange[]>(() => {
    if (!baseline) return []
    const result: DirtyChange[] = []
    const keys = new Set([
      ...Object.keys(baseline.settings),
      ...Object.keys(draft),
    ])
    for (const key of keys) {
      const before = baseline.settings[key]
      const after = draft[key]
      if (!sameSettingValue(before, after)) {
        result.push({ key, before, after })
      }
    }
    return result
  }, [draft, baseline])

  return {
    draft,
    changes,
    isDirty: changes.length > 0,
    setValue,
    reset,
  }
}

function baselineSettings(
  baseline: SecurityProfile | null,
): Record<string, SecuritySettingValue> {
  return baseline ? { ...baseline.settings } : {}
}

function sameSettingValue(
  a: SecuritySettingValue | undefined,
  b: SecuritySettingValue | undefined,
): boolean {
  if (a === b) return true
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false
    return a.every((item, index) => item === b[index])
  }
  return false
}
