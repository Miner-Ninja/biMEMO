import { describe, it, expect } from 'vitest'
import {
  DEFAULT_STREAM_ERROR_MESSAGE,
  chatInputIsDisabled,
  markStreamTerminalError,
  resolveStreamErrorMessage,
  runtimeBootstrapHasTerminalEvent,
  shouldApplyHttpChatResponse,
  type ActiveAssistantStreamState,
} from '@/lib/chat-response-source'

describe('shouldApplyHttpChatResponse', () => {
  it('applies HTTP chat response when no stream final arrived', () => {
    expect(shouldApplyHttpChatResponse(false)).toBe(true)
  })

  it('skips HTTP chat response after stream final arrived', () => {
    expect(shouldApplyHttpChatResponse(true)).toBe(false)
  })
})

describe('markStreamTerminalError', () => {
  it('marks stream error as final enough to suppress HTTP fallback', () => {
    const stream: ActiveAssistantStreamState = {
      assistantMessageId: 'assistant-1',
      conversationId: 'conversation-1',
      requestId: null,
      runId: null,
      voiceUserMessageId: null,
      httpCompleted: false,
      finalReceived: false,
      realtimeCompleted: false,
    }

    expect(markStreamTerminalError(stream)).toEqual({
      ...stream,
      httpCompleted: true,
      finalReceived: true,
    })
  })

  it('does not claim realtime done arrived before the done event', () => {
    const stream: ActiveAssistantStreamState = {
      assistantMessageId: 'assistant-1',
      conversationId: 'conversation-1',
      requestId: null,
      runId: null,
      voiceUserMessageId: null,
      httpCompleted: false,
      finalReceived: false,
      realtimeCompleted: false,
    }

    expect(markStreamTerminalError(stream).realtimeCompleted).toBe(false)
  })
})

describe('resolveStreamErrorMessage', () => {
  it('uses backend stream error message when present', () => {
    expect(resolveStreamErrorMessage('Provider failed')).toBe('Provider failed')
  })

  it('falls back to a stable generic message for empty backend error', () => {
    expect(resolveStreamErrorMessage('   ')).toBe(DEFAULT_STREAM_ERROR_MESSAGE)
    expect(resolveStreamErrorMessage()).toBe(DEFAULT_STREAM_ERROR_MESSAGE)
  })
})

describe('runtimeBootstrapHasTerminalEvent', () => {
  it('returns true when bootstrap contains a terminal run event', () => {
    expect(
      runtimeBootstrapHasTerminalEvent({
        conversation_id: 'conversation-1',
        events: [{ event_type: 'run_completed' }],
      }),
    ).toBe(true)
  })

  it('ignores non-terminal events and payloads without a conversation', () => {
    expect(
      runtimeBootstrapHasTerminalEvent({
        conversation_id: 'conversation-1',
        events: [{ event_type: 'tool_status' }],
      }),
    ).toBe(false)
    expect(
      runtimeBootstrapHasTerminalEvent({
        events: [{ event_type: 'run_completed' }],
      }),
    ).toBe(false)
  })
})

describe('chatInputIsDisabled', () => {
  it('disables input while the backend run is active after reconnect', () => {
    expect(
      chatInputIsDisabled({
        backendRunActive: true,
        localRequestActive: false,
        uploadActive: false,
      }),
    ).toBe(true)
  })

  it('unlocks input after terminal cleanup clears active run state', () => {
    expect(
      chatInputIsDisabled({
        backendRunActive: false,
        localRequestActive: false,
        uploadActive: false,
      }),
    ).toBe(false)
  })
})
