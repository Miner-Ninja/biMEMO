import { useCallback, useEffect, useMemo, useState } from "react"
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

  // The local draft must follow the baseline profile id: when
  // the user activates a different profile, pending edits belong
  // to the previous profile and must be dropped. Effects are the
  // simplest, deterministic place to synchronise this — the
  // during-render-setState alternative recommended by React docs
  // looped against the parent's hook chain on this surface.
  /* eslint-disable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps -- baseline-id sync */
  useEffect(() => {
    setDraft(baselineSettings(baseline))
  }, [baseline?.id])
  /* eslint-enable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */

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
