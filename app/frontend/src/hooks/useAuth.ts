/**
 * Authentication state management for biMEMO.
 * Handles auth status check, refresh, logout, and state tracking.
 * Routes to setup/login/chat based on server auth status.
 */

import { createContext, useContext, useState, useCallback } from 'react'
import { apiRequest, clearCsrfToken, authStatus, type AuthStatus } from '@/lib/bimemo-api'

interface AuthState {
  authenticated: boolean
  loading: boolean
}

export interface AuthContextValue extends AuthState {
  checkStatus: () => Promise<AuthStatus>
  refresh: () => Promise<boolean>
  logout: () => Promise<void>
  setAuthenticated: (value: boolean) => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)

/** Hook to access auth context. Must be used within AuthProvider. */
export function useAuth(): AuthContextValue {
  const authContext = useContext(AuthContext)
  if (!authContext) throw new Error('useAuth must be used within AuthProvider')
  return authContext
}

/** Hook that provides auth state and actions. Used by AuthProvider. */
export function useAuthState(): AuthContextValue {
  const [state, setState] = useState<AuthState>({
    authenticated: false,
    loading: true,
  })

  const checkStatus = useCallback(async () => {
    try {
      const status = await authStatus()
      setState({
        authenticated: status.authenticated,
        loading: false,
      })
      return status
    } catch (error) {
      console.error('Failed to check auth status during app startup:', error)
      setState({
        authenticated: false,
        loading: false,
      })
      return {
        authenticated: false,
        needs_setup: false,
      }
    }
  }, [])

  const refresh = useCallback(async () => {
    const response = await fetch('/refresh', {
      method: 'POST',
      credentials: 'include',
    })
    if (response.ok) {
      setState((prev) => ({ ...prev, authenticated: true }))
    }
    return response.ok
  }, [])

  const logout = useCallback(async () => {
    await apiRequest('/logout', { method: 'POST' })
    clearCsrfToken()
    setState((prev) => ({ ...prev, authenticated: false }))
  }, [])

  const setAuthenticated = useCallback((value: boolean) => {
    setState((prev) => ({ ...prev, authenticated: value }))
  }, [])

  return {
    ...state,
    checkStatus,
    refresh,
    logout,
    setAuthenticated,
  }
}
