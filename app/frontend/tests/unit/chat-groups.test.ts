import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { groupChatsByDate, GROUP_ORDER, type ChatGroup } from '@/lib/chat-groups'
import type { Chat } from '@/types/chat'

// Helper to create a chat with specific date
function createChat(id: string, updatedAt: Date): Chat {
  return {
    id,
    title: `Chat ${id}`,
    messages: [],
    createdAt: updatedAt,
    updatedAt,
  }
}

describe('groupChatsByDate', () => {
  beforeEach(() => {
    // Mock current date to 2024-01-15 12:00:00
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2024-01-15T12:00:00'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('groups chat from today', () => {
    const chats = [createChat('1', new Date('2024-01-15T10:00:00'))]
    const groups = groupChatsByDate(chats)

    expect(groups.get('Today')).toHaveLength(1)
    expect(groups.get('Today')![0].id).toBe('1')
    expect(groups.has('Yesterday')).toBe(false)
  })

  it('groups chat from yesterday', () => {
    const chats = [createChat('1', new Date('2024-01-14T15:00:00'))]
    const groups = groupChatsByDate(chats)

    expect(groups.get('Yesterday')).toHaveLength(1)
    expect(groups.get('Yesterday')![0].id).toBe('1')
    expect(groups.has('Today')).toBe(false)
  })

  it('groups chat from previous 7 days', () => {
    const chats = [createChat('1', new Date('2024-01-10T12:00:00'))]
    const groups = groupChatsByDate(chats)

    expect(groups.get('Previous 7 Days')).toHaveLength(1)
    expect(groups.has('Today')).toBe(false)
    expect(groups.has('Yesterday')).toBe(false)
  })

  it('groups older chat', () => {
    const chats = [createChat('1', new Date('2024-01-01T12:00:00'))]
    const groups = groupChatsByDate(chats)

    expect(groups.get('Older')).toHaveLength(1)
    expect(groups.has('Previous 7 Days')).toBe(false)
  })

  it('groups multiple chats into correct groups', () => {
    const chats = [
      createChat('today', new Date('2024-01-15T08:00:00')),
      createChat('yesterday', new Date('2024-01-14T20:00:00')),
      createChat('week', new Date('2024-01-12T12:00:00')),
      createChat('old', new Date('2024-01-01T12:00:00')),
    ]
    const groups = groupChatsByDate(chats)

    expect(groups.get('Today')).toHaveLength(1)
    expect(groups.get('Yesterday')).toHaveLength(1)
    expect(groups.get('Previous 7 Days')).toHaveLength(1)
    expect(groups.get('Older')).toHaveLength(1)
  })

  it('sorts chats within groups by updatedAt descending', () => {
    const chats = [
      createChat('1', new Date('2024-01-15T08:00:00')),
      createChat('2', new Date('2024-01-15T10:00:00')),
      createChat('3', new Date('2024-01-15T09:00:00')),
    ]
    const groups = groupChatsByDate(chats)
    const todayChats = groups.get('Today')!

    expect(todayChats[0].id).toBe('2') // 10:00 - most recent
    expect(todayChats[1].id).toBe('3') // 09:00
    expect(todayChats[2].id).toBe('1') // 08:00 - oldest
  })

  it('returns empty map for empty input', () => {
    const groups = groupChatsByDate([])
    expect(groups.size).toBe(0)
  })
})

describe('GROUP_ORDER', () => {
  it('has correct order', () => {
    expect(GROUP_ORDER).toEqual(['Today', 'Yesterday', 'Previous 7 Days', 'Older'])
  })

  it('contains all ChatGroup types', () => {
    const allGroups: ChatGroup[] = ['Today', 'Yesterday', 'Previous 7 Days', 'Older']
    expect(GROUP_ORDER).toEqual(allGroups)
  })
})
