import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { SecuritySetting } from '@/api/security'
import { DiffBar } from '@/pages/settings/security/components/DiffBar'

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

function makeSetting(key: string, label: string): SecuritySetting {
  return {
    key,
    category: 'input_filter',
    label,
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
  }
}

describe('DiffBar', () => {
  it('renders nothing when there are no pending changes', () => {
    const { container } = render(
      <DiffBar
        changes={[]}
        activeProfileLabel="personal"
        isBuiltinActive
        catalogByKey={{}}
        onSaveAsNew={() => {}}
        onDiscard={() => {}}
        onOverwrite={() => {}}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('disables the Overwrite action when the active profile is built-in', () => {
    render(
      <DiffBar
        changes={[{ key: 'foo', before: true, after: false }]}
        activeProfileLabel="personal"
        isBuiltinActive
        catalogByKey={{ foo: makeSetting('foo', 'Foo toggle') }}
        onSaveAsNew={() => {}}
        onDiscard={() => {}}
        onOverwrite={() => {}}
      />,
    )

    const overwrite = screen.getByRole('button', {
      name: 'Перезаписать профиль',
    })
    expect(overwrite).toBeDisabled()
  })

  it('enables the Overwrite action for custom profiles', () => {
    render(
      <DiffBar
        changes={[{ key: 'foo', before: true, after: false }]}
        activeProfileLabel="my-dev"
        isBuiltinActive={false}
        catalogByKey={{ foo: makeSetting('foo', 'Foo toggle') }}
        onSaveAsNew={() => {}}
        onDiscard={() => {}}
        onOverwrite={() => {}}
      />,
    )
    expect(
      screen.getByRole('button', { name: 'Перезаписать профиль' }),
    ).toBeEnabled()
  })

  it('renders one human-friendly diff line per change after expansion', () => {
    render(
      <DiffBar
        changes={[
          { key: 'foo', before: true, after: false },
          { key: 'limit', before: 10, after: 50 },
        ]}
        activeProfileLabel="personal"
        isBuiltinActive
        catalogByKey={{
          foo: makeSetting('foo', 'Foo toggle'),
          limit: makeSetting('limit', 'Iteration cap'),
        }}
        onSaveAsNew={() => {}}
        onDiscard={() => {}}
        onOverwrite={() => {}}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Показать изменения/ }))
    expect(screen.getByText('Foo toggle: ВКЛ → ВЫКЛ')).toBeInTheDocument()
    expect(screen.getByText('Iteration cap: 10 → 50')).toBeInTheDocument()
  })

  it('forwards Save / Discard callbacks', () => {
    const onSaveAsNew = vi.fn()
    const onDiscard = vi.fn()
    render(
      <DiffBar
        changes={[{ key: 'foo', before: true, after: false }]}
        activeProfileLabel="personal"
        isBuiltinActive
        catalogByKey={{ foo: makeSetting('foo', 'Foo toggle') }}
        onSaveAsNew={onSaveAsNew}
        onDiscard={onDiscard}
        onOverwrite={() => {}}
      />,
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Сохранить как новый профиль' }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Отменить изменения' }),
    )

    expect(onSaveAsNew).toHaveBeenCalledTimes(1)
    expect(onDiscard).toHaveBeenCalledTimes(1)
  })
})
