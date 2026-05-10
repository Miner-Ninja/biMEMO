import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useRealtimeStream } from '@/hooks/useRealtimeStream'

vi.mock('@/lib/bimemo-api', () => ({
  authStatus: vi.fn(async () => ({ authenticated: true })),
  refreshAccessToken: vi.fn(async () => true),
}))

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  url: string

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close() {
    this.onclose?.()
  }
}

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: (() => void) | null = null
  url: string

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  close() {}
}

describe('useRealtimeStream', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    FakeEventSource.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('dispatches WebSocket messages with the shared event contract', async () => {
    const onEvent = vi.fn()
    const { result } = renderHook(() => useRealtimeStream(onEvent, 'conv-1'))

    await act(async () => {
      await result.current.connect()
    })

    const socket = FakeWebSocket.instances[0]
    expect(socket.url).toContain('/ws?conversation_id=conv-1')

    act(() => {
      socket.onopen?.()
      socket.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({ type: 'final', request_id: 'req-1' }),
        }),
      )
    })

    expect(onEvent).toHaveBeenCalledWith({ type: 'final', request_id: 'req-1' })
    expect(FakeEventSource.instances).toHaveLength(0)
  })

  it('falls back to SSE when WebSocket closes before opening', async () => {
    const onEvent = vi.fn()
    const { result } = renderHook(() => useRealtimeStream(onEvent, 'conv-2'))

    await act(async () => {
      await result.current.connect()
    })

    const socket = FakeWebSocket.instances[0]
    act(() => {
      socket.onclose?.()
    })

    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1)
    })

    const source = FakeEventSource.instances[0]
    expect(source.url).toBe('/stream?conversation_id=conv-2')
    act(() => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({ type: 'done', request_id: 'req-2' }),
        }),
      )
    })

    expect(onEvent).toHaveBeenCalledWith({ type: 'done', request_id: 'req-2' })
  })
})
