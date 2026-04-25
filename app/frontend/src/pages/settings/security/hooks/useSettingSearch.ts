import { useMemo } from "react"
import type { SecuritySetting } from "@/api/security"

export interface UseSettingSearchResult {
  filtered: SecuritySetting[]
  matchingCategories: Set<string>
}

export function useSettingSearch(
  settings: SecuritySetting[],
  query: string,
): UseSettingSearchResult {
  return useMemo<UseSettingSearchResult>(() => {
    const trimmed = query.trim().toLowerCase()
    if (!trimmed) {
      return {
        filtered: settings,
        matchingCategories: new Set(settings.map((entry) => entry.category)),
      }
    }
    const filtered = settings.filter((entry) => matches(entry, trimmed))
    const matchingCategories = new Set(filtered.map((entry) => entry.category))
    return { filtered, matchingCategories }
  }, [settings, query])
}

function matches(entry: SecuritySetting, needle: string): boolean {
  if (entry.label.toLowerCase().includes(needle)) return true
  if (entry.description.toLowerCase().includes(needle)) return true
  if (entry.category.toLowerCase().includes(needle)) return true
  if (entry.example && entry.example.toLowerCase().includes(needle)) return true
  return entry.keywords.some((keyword) =>
    keyword.toLowerCase().includes(needle),
  )
}
