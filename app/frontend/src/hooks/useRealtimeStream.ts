/**
 * Realtime stream hook with WebSocket primary transport and SSE fallback.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { authStatus, refreshAccessToken } from '@/lib/bimemo-api'
import type { ConnectionStatus, SSEEvent } from '@/hooks/useSSE'

const MIN_BACKOFF = 1000
const MAX_BACKOFF = 30000
const WS_EARLY_CLOSE_MS = 1500

function streamUrl(conversationId?: string | null): string {
  if (!conversationId) {
    return '/stream'
  }
  return `/stream?conversation_id=${encodeURIComponent(conversationId)}`
}

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
  onEvent: (event: SSEEvent) => void,
  conversationId?: string | null,
) {
  const [status, setStatus] = useState<ConnectionStatus>('disconnected')
  const webSocketRef = useRef<WebSocket | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
  const backoffRef = useRef(MIN_BACKOFF)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const activeRef = useRef(false)
  const onEventRef = useRef(onEvent)
  const connectRef = useRef<(() => Promise<void>) | null>(null)
  const openedAtRef = useRef(0)
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
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
      eventSourceRef.current = null
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
      const data: SSEEvent = JSON.parse(rawData)
      if (data.type === 'heartbeat') {
        return
      }
      onEventRef.current(data)
    } catch {
      /* ignore malformed realtime messages */
    }
  }, [])

  const connectSSE = useCallback(async () => {
    const authenticated = await ensureAuthenticated()
    if (!authenticated) {
      setStatus('disconnected')
      scheduleReconnect()
      return
    }

    const source = new EventSource(streamUrl(conversationId))
    eventSourceRef.current = source

    source.onopen = () => {
      setStatus('connected')
      backoffRef.current = MIN_BACKOFF
    }

    source.onmessage = (event) => {
      dispatchEvent(event.data)
    }

    source.onerror = () => {
      source.close()
      if (eventSourceRef.current === source) {
        eventSourceRef.current = null
      }
      setStatus('disconnected')
      scheduleReconnect()
    }
  }, [conversationId, dispatchEvent, scheduleReconnect])

  const connectWebSocket = useCallback(async () => {
    const authenticated = await ensureAuthenticated()
    if (!authenticated) {
      setStatus('disconnected')
      scheduleReconnect()
      return
    }

    const socket = new WebSocket(websocketUrl(conversationId))
    webSocketRef.current = socket
    openedAtRef.current = 0

    socket.onopen = () => {
      openedAtRef.current = Date.now()
      setStatus('connected')
      backoffRef.current = MIN_BACKOFF
    }

    socket.onmessage = (event) => {
      if (typeof event.data === 'string') {
        dispatchEvent(event.data)
      }
    }

    socket.onerror = () => {
      if (!openedAtRef.current && activeRef.current) {
        socket.close()
      }
    }

    socket.onclose = () => {
      if (webSocketRef.current === socket) {
        webSocketRef.current = null
      }
      setStatus('disconnected')
      if (!activeRef.current) return

      const connectedDuration = openedAtRef.current
        ? Date.now() - openedAtRef.current
        : 0
      const shouldFallbackToSSE =
        !openedAtRef.current || connectedDuration < WS_EARLY_CLOSE_MS
      if (shouldFallbackToSSE) {
        void connectSSE()
        return
      }
      scheduleReconnect()
    }
  }, [connectSSE, conversationId, dispatchEvent, scheduleReconnect])

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
