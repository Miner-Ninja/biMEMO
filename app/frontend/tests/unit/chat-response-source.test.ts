import { describe, it, expect } from 'vitest'
import {
  DEFAULT_STREAM_ERROR_MESSAGE,
  markStreamTerminalError,
  resolveStreamErrorMessage,
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
      voiceUserMessageId: null,
      httpCompleted: false,
      finalReceived: false,
      sseCompleted: false,
    }

    expect(markStreamTerminalError(stream)).toEqual({
      ...stream,
      httpCompleted: true,
      finalReceived: true,
    })
  })

  it('does not claim SSE done arrived before the done event', () => {
    const stream: ActiveAssistantStreamState = {
      assistantMessageId: 'assistant-1',
      conversationId: 'conversation-1',
      voiceUserMessageId: null,
      httpCompleted: false,
      finalReceived: false,
      sseCompleted: false,
    }

    expect(markStreamTerminalError(stream).sseCompleted).toBe(false)
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
