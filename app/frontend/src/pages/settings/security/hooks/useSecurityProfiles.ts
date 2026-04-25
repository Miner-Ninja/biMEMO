import { useCallback, useEffect, useState } from "react"
import {
  activateSecurityProfile,
  createSecurityProfile,
  deleteSecurityProfile,
  listSecurityProfiles,
  updateSecurityProfile,
  type CreateProfileRequest,
  type SecurityProfile,
  type UpdateProfileRequest,
} from "@/api/security"

export interface UseSecurityProfilesResult {
  profiles: SecurityProfile[]
  loading: boolean
  error: string
  refetch: () => Promise<void>
  create: (body: CreateProfileRequest) => Promise<SecurityProfile>
  update: (id: string, body: UpdateProfileRequest) => Promise<SecurityProfile>
  remove: (id: string) => Promise<{ deleted_id: string; new_active_id: string | null }>
  activate: (id: string) => Promise<string>
}

export function useSecurityProfiles(): UseSecurityProfilesResult {
  const [profiles, setProfiles] = useState<SecurityProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const refetch = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listSecurityProfiles()
      setProfiles(data)
      setError("")
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load profiles")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refetch()
  }, [refetch])

  const create = useCallback(
    async (body: CreateProfileRequest) => {
      const profile = await createSecurityProfile(body)
      await refetch()
      return profile
    },
    [refetch],
  )

  const update = useCallback(
    async (id: string, body: UpdateProfileRequest) => {
      const profile = await updateSecurityProfile(id, body)
      await refetch()
      return profile
    },
    [refetch],
  )

  const remove = useCallback(
    async (id: string) => {
      const result = await deleteSecurityProfile(id)
      await refetch()
      return result
    },
    [refetch],
  )

  const activate = useCallback(
    async (id: string) => {
      const result = await activateSecurityProfile(id)
      return result.active_profile_id
    },
    [],
  )

  return {
    profiles,
    loading,
    error,
    refetch,
    create,
    update,
    remove,
    activate,
  }
}
