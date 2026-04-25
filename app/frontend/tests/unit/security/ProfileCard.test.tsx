import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { SecurityProfile } from '@/api/security'
import { ProfileCard } from '@/pages/settings/security/components/ProfileCard'

const TIMESTAMP = '2026-01-01T00:00:00.000'

function makeProfile(overrides: Partial<SecurityProfile> = {}): SecurityProfile {
  return {
    id: 'personal',
    name: 'personal',
    description: 'Default profile',
    is_builtin: true,
    settings: {},
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    ...overrides,
  }
}

describe('ProfileCard', () => {
  it('renders the built-in display label and active badge when active', () => {
    render(
      <ProfileCard
        profile={makeProfile({ id: 'personal', is_builtin: true })}
        isActive
        changedCount={0}
        onActivate={() => {}}
        onClone={() => {}}
        onRename={() => {}}
        onDelete={() => {}}
      />,
    )

    expect(screen.getByText('Обычный')).toBeInTheDocument()
    // "Активен" appears both in the badge and on the disabled button.
    expect(screen.getAllByText('Активен').length).toBeGreaterThan(0)
  })

  it('shows a "Изменён N" badge segment when there are unsaved changes', () => {
    render(
      <ProfileCard
        profile={makeProfile({ id: 'personal', is_builtin: true })}
        isActive
        changedCount={3}
        onActivate={() => {}}
        onClone={() => {}}
        onRename={() => {}}
        onDelete={() => {}}
      />,
    )

    expect(
      screen.getByText(/Активен · Изменён 3/),
    ).toBeInTheDocument()
  })

  it('exposes only the Clone action for built-in profiles', async () => {
    const user = userEvent.setup()
    render(
      <ProfileCard
        profile={makeProfile({ id: 'personal', is_builtin: true })}
        isActive={false}
        changedCount={0}
        onActivate={() => {}}
        onClone={() => {}}
        onRename={() => {}}
        onDelete={() => {}}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Действия с профилем' }))
    expect(await screen.findByText('Клонировать')).toBeInTheDocument()
    expect(screen.queryByText('Переименовать')).not.toBeInTheDocument()
    expect(screen.queryByText('Удалить')).not.toBeInTheDocument()
  })

  it('exposes Clone + Rename + Delete for custom profiles', async () => {
    const user = userEvent.setup()
    render(
      <ProfileCard
        profile={makeProfile({
          id: 'my-dev',
          name: 'my-dev',
          is_builtin: false,
        })}
        isActive={false}
        changedCount={0}
        onActivate={() => {}}
        onClone={() => {}}
        onRename={() => {}}
        onDelete={() => {}}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Действия с профилем' }))
    expect(await screen.findByText('Клонировать')).toBeInTheDocument()
    expect(screen.getByText('Переименовать')).toBeInTheDocument()
    expect(screen.getByText('Удалить')).toBeInTheDocument()
  })

  it('forwards the activate callback when the button is clicked', () => {
    const onActivate = vi.fn()
    render(
      <ProfileCard
        profile={makeProfile({ id: 'developer', is_builtin: true })}
        isActive={false}
        changedCount={0}
        onActivate={onActivate}
        onClone={() => {}}
        onRename={() => {}}
        onDelete={() => {}}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Выбрать' }))
    expect(onActivate).toHaveBeenCalledTimes(1)
  })

  it('disables the activate button when the profile is already active', () => {
    render(
      <ProfileCard
        profile={makeProfile({ id: 'personal', is_builtin: true })}
        isActive
        changedCount={0}
        onActivate={() => {}}
        onClone={() => {}}
        onRename={() => {}}
        onDelete={() => {}}
      />,
    )

    const buttons = screen.getAllByRole('button', { name: 'Активен' })
    expect(buttons.some((button) => button.hasAttribute('disabled'))).toBe(true)
  })
})
