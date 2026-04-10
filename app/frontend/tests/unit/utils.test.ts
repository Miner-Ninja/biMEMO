import { describe, it, expect } from 'vitest'
import { cn, generateId, formatDate, truncate } from '@/lib/utils'

describe('cn', () => {
  it('merges class names', () => {
    expect(cn('foo', 'bar')).toBe('foo bar')
  })

  it('handles conditional classes', () => {
    expect(cn('base', true && 'active', false && 'hidden')).toBe('base active')
  })

  it('merges tailwind classes correctly', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4')
    expect(cn('text-red-500', 'text-blue-500')).toBe('text-blue-500')
  })

  it('handles empty input', () => {
    expect(cn()).toBe('')
  })
})

describe('generateId', () => {
  it('returns a string', () => {
    const id = generateId()
    expect(typeof id).toBe('string')
  })

  it('returns a valid UUID format', () => {
    const id = generateId()
    const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
    expect(id).toMatch(uuidRegex)
  })

  it('generates unique IDs', () => {
    const ids = new Set(Array.from({ length: 100 }, () => generateId()))
    expect(ids.size).toBe(100)
  })
})

describe('formatDate', () => {
  it('formats date as HH:MM', () => {
    const date = new Date('2024-01-15T14:30:00')
    expect(formatDate(date)).toBe('14:30')
  })

  it('pads single digits with zero', () => {
    const date = new Date('2024-01-15T09:05:00')
    expect(formatDate(date)).toBe('09:05')
  })

  it('handles midnight', () => {
    const date = new Date('2024-01-15T00:00:00')
    expect(formatDate(date)).toBe('00:00')
  })

  it('handles noon', () => {
    const date = new Date('2024-01-15T12:00:00')
    expect(formatDate(date)).toBe('12:00')
  })
})

describe('truncate', () => {
  it('returns original string if shorter than maxLength', () => {
    expect(truncate('hello', 10)).toBe('hello')
  })

  it('returns original string if equal to maxLength', () => {
    expect(truncate('hello', 5)).toBe('hello')
  })

  it('truncates long strings with ellipsis', () => {
    expect(truncate('hello world', 8)).toBe('hello...')
  })

  it('handles empty string', () => {
    expect(truncate('', 10)).toBe('')
  })

  it('handles maxLength of 3 (minimum for ellipsis)', () => {
    expect(truncate('hello', 3)).toBe('...')
  })
})
