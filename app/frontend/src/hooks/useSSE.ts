/**
 * SSE client hook with auto-reconnect and exponential backoff.
 * Connects to the /stream endpoint for real-time token streaming.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { authStatus, refreshAccessToken } from '@/lib/bimemo-api'

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'

export interface SSEEvent {
  type: string
  content?: string
  [key: string]: unknown
}

const MIN_BACKOFF = 1000
const MAX_BACKOFF = 30000

/**
 * React hook for SSE streaming with auto-reconnect.
 *
 * Connects to /stream and dispatches events via onEvent callback.
 * Reconnects with exponential backoff (1s-30s) on disconnection.
 */
function streamUrl(conversationId?: string | null): string {
  if (!conversationId) {
    return '/stream'
  }
  return `/stream?conversation_id=${encodeURIComponent(conversationId)}`
}

export function useSSE(
  onEvent: (event: SSEEvent) => void,
  conversationId?: string | null,
) {
  const [status, setStatus] = useState<ConnectionStatus>('disconnected')
  const sourceRef = useRef<EventSource | null>(null)
  const backoffRef = useRef(MIN_BACKOFF)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const activeRef = useRef(false)
  const lastHeartbeatRef = useRef<number>(0)
  const onEventRef = useRef(onEvent)
  const connectRef = useRef<(() => Promise<void>) | null>(null)
  const reconnectingRef = useRef(false)

  useEffect(() => {
    onEventRef.current = onEvent
  }, [onEvent])

  const cleanup = useCallback(() => {
    if (retryRef.current) {
      clearTimeout(retryRef.current)
      retryRef.current = null
    }
    if (sourceRef.current) {
      sourceRef.current.close()
      sourceRef.current = null
    }
  }, [])

  const connectSSE = useCallback(async () => {
    cleanup()
    activeRef.current = true
    setStatus('connecting')

    try {
      const status = await authStatus()
      if (!status.authenticated) {
        const refreshed = await refreshAccessToken()
        if (!refreshed) {
          setStatus('disconnected')
          if (activeRef.current) {
            const delay = backoffRef.current
            backoffRef.current = Math.min(delay * 2, MAX_BACKOFF)
            retryRef.current = setTimeout(() => {
              void connectRef.current?.()
            }, delay)
          }
          return
        }
      }
    } catch {
      setStatus('disconnected')
      if (activeRef.current) {
        const delay = backoffRef.current
        backoffRef.current = Math.min(delay * 2, MAX_BACKOFF)
        retryRef.current = setTimeout(() => {
          void connectRef.current?.()
        }, delay)
      }
      return
    }

    const source = new EventSource(streamUrl(conversationId))
    sourceRef.current = source

    source.onopen = () => {
      setStatus('connected')
      backoffRef.current = MIN_BACKOFF
    }

    source.onmessage = (event) => {
      try {
        const data: SSEEvent = JSON.parse(event.data)
        if (data.type === 'heartbeat') {
          lastHeartbeatRef.current = Date.now()
          return
        }
        onEventRef.current(data)
      } catch {
        /* ignore parse errors */
      }
    }

    source.onerror = () => {
      source.close()
      sourceRef.current = null
      setStatus('disconnected')

      if (!activeRef.current || reconnectingRef.current) return

      reconnectingRef.current = true
      void refreshAccessToken()
        .catch(() => false)
        .finally(() => {
          reconnectingRef.current = false
          if (!activeRef.current) return
          const delay = backoffRef.current
          backoffRef.current = Math.min(delay * 2, MAX_BACKOFF)
          retryRef.current = setTimeout(() => {
            void connectRef.current?.()
          }, delay)
        })
    }
  }, [cleanup, conversationId])
  useEffect(() => {
    connectRef.current = connectSSE
  }, [connectSSE])

  const disconnect = useCallback(() => {
    activeRef.current = false
    cleanup()
    setStatus('disconnected')
  }, [cleanup])

  useEffect(() => {
    return () => {
      activeRef.current = false
      cleanup()
    }
  }, [cleanup])

  return { status, connect: connectSSE, disconnect }
}
