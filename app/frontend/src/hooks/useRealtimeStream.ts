/**
 * WebSocket-only realtime stream hook.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { authStatus, refreshAccessToken } from '@/lib/bimemo-api'

const MIN_BACKOFF = 1000
const MAX_BACKOFF = 30000

export interface RealtimeEvent {
  type: string
  data?: unknown
  content?: string
  request_id?: string
}

export type ConnectionStatus = 'connected' | 'connecting' | 'disconnected'

function websocketUrl(conversationId?: string | null): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = new URL('/ws', `${protocol}//${window.location.host}`)
  if (conversationId) {
    url.searchParams.set('conversation_id', conversationId)
  }
  return url.toString()
}

async function ensureAuthenticated(): Promise<boolean> {
  try {
    const status = await authStatus()
    if (status.authenticated) {
      return true
    }
    return await refreshAccessToken()
  } catch {
    return false
  }
}

export function useRealtimeStream(
  onEvent: (event: RealtimeEvent) => void,
  conversationId?: string | null,
) {
  const [status, setStatus] = useState<ConnectionStatus>('disconnected')
  const webSocketRef = useRef<WebSocket | null>(null)
  const backoffRef = useRef(MIN_BACKOFF)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const activeRef = useRef(false)
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
    if (webSocketRef.current) {
      webSocketRef.current.close()
      webSocketRef.current = null
    }
  }, [])

  const scheduleReconnect = useCallback(() => {
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
  }, [])

  const dispatchEvent = useCallback((rawData: string) => {
    try {
      const data: RealtimeEvent = JSON.parse(rawData)
      if (data.type === 'heartbeat') {
        return
      }
      onEventRef.current(data)
    } catch {
      /* ignore malformed realtime messages */
    }
  }, [])

  const connectWebSocket = useCallback(async () => {
    const authenticated = await ensureAuthenticated()
    if (!authenticated) {
      setStatus('disconnected')
      scheduleReconnect()
      return
    }

    const socket = new WebSocket(websocketUrl(conversationId))
    webSocketRef.current = socket

    socket.onopen = () => {
      setStatus('connected')
      backoffRef.current = MIN_BACKOFF
    }

    socket.onmessage = (event) => {
      if (typeof event.data === 'string') {
        dispatchEvent(event.data)
      }
    }

    socket.onerror = () => {
      if (activeRef.current) {
        socket.close()
      }
    }

    socket.onclose = () => {
      if (webSocketRef.current === socket) {
        webSocketRef.current = null
      }
      setStatus('disconnected')
      if (!activeRef.current) return
      scheduleReconnect()
    }
  }, [conversationId, dispatchEvent, scheduleReconnect])

  const connect = useCallback(async () => {
    activeRef.current = false
    cleanup()
    activeRef.current = true
    setStatus('connecting')
    await connectWebSocket()
  }, [cleanup, connectWebSocket])

  useEffect(() => {
    connectRef.current = connect
  }, [connect])

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

  return { status, connect, disconnect }
}
