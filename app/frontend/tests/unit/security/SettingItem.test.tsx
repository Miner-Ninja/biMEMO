import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { SecuritySetting } from '@/api/security'
import { SettingItem } from '@/pages/settings/security/components/SettingItem'

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
  overrides: Partial<SecuritySetting> & Pick<SecuritySetting, 'key' | 'type' | 'risk_level' | 'label'>,
): SecuritySetting {
  return {
    category: 'input_filter',
    description: 'Test setting description',
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

describe('SettingItem', () => {
  it('renders a Switch for toggle settings and forwards changes', () => {
    const onChange = vi.fn()
    const setting = makeSetting({
      key: 'sample.toggle',
      type: 'toggle',
      risk_level: 'high',
      label: 'Sample toggle',
    })

    render(
      <SettingItem
        setting={setting}
        value
        baselineValue
        onChange={onChange}
      />,
    )

    expect(screen.getByText('Sample toggle')).toBeInTheDocument()
    const toggle = screen.getByRole('switch')
    expect(toggle).toHaveAttribute('aria-checked', 'true')

    fireEvent.click(toggle)
    expect(onChange).toHaveBeenCalledWith(false)
  })

  it('shows the inline destructive alert only when a protective toggle moves OFF', () => {
    const setting = makeSetting({
      key: 'sample.protective',
      type: 'toggle',
      risk_level: 'high',
      label: 'Protective toggle',
    })

    const { rerender } = render(
      <SettingItem
        setting={setting}
        value
        baselineValue
        onChange={() => {}}
      />,
    )
    expect(screen.queryByText('Защита выключена.')).not.toBeInTheDocument()

    rerender(
      <SettingItem
        setting={setting}
        value={false}
        baselineValue
        onChange={() => {}}
      />,
    )
    expect(screen.getByText('Защита выключена.')).toBeInTheDocument()
  })

  it('does not surface the warning for low-risk toggles', () => {
    const setting = makeSetting({
      key: 'sample.low',
      type: 'toggle',
      risk_level: 'low',
      label: 'Low risk toggle',
    })

    render(
      <SettingItem
        setting={setting}
        value={false}
        baselineValue
        onChange={() => {}}
      />,
    )
    expect(screen.queryByText('Защита выключена.')).not.toBeInTheDocument()
  })

  it('surfaces the example mini-scenario when present', () => {
    const setting = makeSetting({
      key: 'sample.with_example',
      type: 'toggle',
      risk_level: 'medium',
      label: 'Example carrier',
      example: 'Try: paste OPENAI_API_KEY=sk-abc',
    })

    render(
      <SettingItem
        setting={setting}
        value
        baselineValue
        onChange={() => {}}
      />,
    )
    expect(
      screen.getByText('💡 Try: paste OPENAI_API_KEY=sk-abc'),
    ).toBeInTheDocument()
  })
})
