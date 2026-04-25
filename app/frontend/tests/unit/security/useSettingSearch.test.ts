import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { SecuritySetting } from '@/api/security'
import { useSettingSearch } from '@/pages/settings/security/hooks/useSettingSearch'

const NO_CONSTRAINTS = {
  min: null,
  max: null,
  step: null,
  unit: null,
  options: null,
  item_regex: null,
  max_length: null,
  placeholder: null,
}

function makeSetting(
  overrides: Partial<SecuritySetting> & Pick<SecuritySetting, 'key' | 'category' | 'label'>,
): SecuritySetting {
  return {
    description: '',
    type: 'toggle',
    risk_level: 'low',
    constraints: NO_CONSTRAINTS,
    default_paranoid: true,
    default_personal: true,
    default_developer: true,
    rationale: null,
    example: null,
    keywords: [],
    locked: false,
    ...overrides,
  }
}

const SAMPLE: SecuritySetting[] = [
  makeSetting({
    key: 'input_filter.block_credentials_strict',
    category: 'input_filter',
    label: 'Блокировать вставку API-ключей',
    description: 'Срабатывает на sk-..., ghp_..., AKIA...',
    keywords: ['ключ', 'token', 'credential'],
  }),
  makeSetting({
    key: 'network.https_only',
    category: 'network',
    label: 'Только HTTPS-соединения',
    description: 'Запрещает обычный HTTP трафик.',
    keywords: ['https', 'tls'],
  }),
  makeSetting({
    key: 'agent_limits.iterations.max',
    category: 'agent_limits',
    label: 'Максимум шагов агента',
    description: 'Сколько шагов агент может сделать.',
    example: 'Поднимите до 80 для длинных задач.',
    keywords: ['iterations'],
  }),
]

describe('useSettingSearch', () => {
  it('returns every setting when the query is empty', () => {
    const { result } = renderHook(() => useSettingSearch(SAMPLE, ''))
    expect(result.current.filtered).toHaveLength(SAMPLE.length)
    expect(result.current.matchingCategories.size).toBe(3)
  })

  it('matches against label text', () => {
    const { result } = renderHook(() => useSettingSearch(SAMPLE, 'API-ключей'))
    expect(result.current.filtered.map((entry) => entry.key)).toEqual([
      'input_filter.block_credentials_strict',
    ])
  })

  it('matches against description text', () => {
    const { result } = renderHook(() => useSettingSearch(SAMPLE, 'трафик'))
    expect(result.current.filtered.map((entry) => entry.key)).toEqual([
      'network.https_only',
    ])
  })

  it('matches against keywords', () => {
    const { result } = renderHook(() => useSettingSearch(SAMPLE, 'token'))
    expect(result.current.filtered.map((entry) => entry.key)).toEqual([
      'input_filter.block_credentials_strict',
    ])
  })

  it('matches against the example field when present', () => {
    const { result } = renderHook(() => useSettingSearch(SAMPLE, 'длинных'))
    expect(result.current.filtered.map((entry) => entry.key)).toEqual([
      'agent_limits.iterations.max',
    ])
  })

  it('matches against the category id', () => {
    const { result } = renderHook(() => useSettingSearch(SAMPLE, 'network'))
    expect(result.current.filtered.map((entry) => entry.key)).toEqual([
      'network.https_only',
    ])
  })

  it('is case-insensitive', () => {
    const { result } = renderHook(() => useSettingSearch(SAMPLE, 'HTTPS'))
    expect(result.current.filtered.map((entry) => entry.key)).toEqual([
      'network.https_only',
    ])
  })

  it('returns an empty filter set when nothing matches', () => {
    const { result } = renderHook(() => useSettingSearch(SAMPLE, 'нет такого'))
    expect(result.current.filtered).toHaveLength(0)
    expect(result.current.matchingCategories.size).toBe(0)
  })
})
