import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { SecurityProfile } from '@/api/security'
import { useDirtySettings } from '@/pages/settings/security/hooks/useDirtySettings'

const TIMESTAMP = '2026-01-01T00:00:00.000'

function makeProfile(
  id: string,
  settings: Record<string, unknown>,
): SecurityProfile {
  return {
    id,
    name: id,
    description: '',
    is_builtin: false,
    settings: settings as SecurityProfile['settings'],
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
  }
}

describe('useDirtySettings', () => {
  it('reports no changes when draft matches baseline', () => {
    const baseline = makeProfile('a', { foo: true, bar: 10 })
    const { result } = renderHook(() => useDirtySettings(baseline))

    expect(result.current.isDirty).toBe(false)
    expect(result.current.changes).toEqual([])
    expect(result.current.draft).toEqual({ foo: true, bar: 10 })
  })

  it('records a single change when one value is updated', () => {
    const baseline = makeProfile('a', { foo: true, bar: 10 })
    const { result } = renderHook(() => useDirtySettings(baseline))

    act(() => result.current.setValue('foo', false))

    expect(result.current.isDirty).toBe(true)
    expect(result.current.changes).toEqual([
      { key: 'foo', before: true, after: false },
    ])
  })

  it('treats list values element-wise via deep comparison', () => {
    const baseline = makeProfile('a', { allow: ['.py', '.md'] })
    const { result } = renderHook(() => useDirtySettings(baseline))

    act(() => result.current.setValue('allow', ['.py', '.md']))
    expect(result.current.isDirty).toBe(false)

    act(() => result.current.setValue('allow', ['.py', '.md', '.go']))
    expect(result.current.isDirty).toBe(true)
  })

  it('reset() returns the draft to the baseline values', () => {
    const baseline = makeProfile('a', { foo: true })
    const { result } = renderHook(() => useDirtySettings(baseline))

    act(() => result.current.setValue('foo', false))
    expect(result.current.isDirty).toBe(true)

    act(() => result.current.reset())
    expect(result.current.isDirty).toBe(false)
    expect(result.current.draft).toEqual({ foo: true })
  })

  it('drops pending edits when the baseline profile id changes', () => {
    let baseline = makeProfile('a', { foo: true, bar: 10 })
    const { result, rerender } = renderHook(
      (next: SecurityProfile) => useDirtySettings(next),
      { initialProps: baseline },
    )

    act(() => result.current.setValue('foo', false))
    expect(result.current.isDirty).toBe(true)

    baseline = makeProfile('b', { foo: true, bar: 10 })
    rerender(baseline)

    expect(result.current.isDirty).toBe(false)
    expect(result.current.draft).toEqual({ foo: true, bar: 10 })
  })

  it('handles a null baseline as an empty draft', () => {
    const { result } = renderHook(() => useDirtySettings(null))

    expect(result.current.draft).toEqual({})
    expect(result.current.isDirty).toBe(false)
  })
})
