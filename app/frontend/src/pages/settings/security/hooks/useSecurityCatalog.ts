import { useCallback, useEffect, useState } from "react"
import {
  getSecurityCatalog,
  type SecurityCatalog,
} from "@/api/security"

export interface UseSecurityCatalogResult {
  catalog: SecurityCatalog | null
  loading: boolean
  error: string
}

export function useSecurityCatalog(): UseSecurityCatalogResult {
  const [catalog, setCatalog] = useState<SecurityCatalog | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const fetchCatalog = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getSecurityCatalog()
      setCatalog(data)
      setError("")
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load catalog")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchCatalog()
  }, [fetchCatalog])

  return { catalog, loading, error }
}
