import { useCallback, useEffect, useState } from "react"
import {
  getActiveSecurityProfile,
  type SecurityProfile,
} from "@/api/security"

export interface UseActiveProfileResult {
  activeId: string | null
  profile: SecurityProfile | null
  loading: boolean
  error: string
  refetch: () => Promise<void>
}

export function useActiveProfile(): UseActiveProfileResult {
  const [activeId, setActiveId] = useState<string | null>(null)
  const [profile, setProfile] = useState<SecurityProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const refetch = useCallback(async () => {
    setLoading(true)
    try {
      const result = await getActiveSecurityProfile()
      setActiveId(result.active_profile_id)
      setProfile(result.profile)
      setError("")
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to load active profile",
      )
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refetch()
  }, [refetch])

  return { activeId, profile, loading, error, refetch }
}
