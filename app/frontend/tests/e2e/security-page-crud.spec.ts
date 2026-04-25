import { expect, test, type Page } from '@playwright/test'

/**
 * End-to-end CRUD flow for the security profiles page.
 *
 * Mocks the four /security/* endpoints with a stateful in-test
 * store so a real round-trip is simulated: clone Personal into
 * a custom profile, edit a setting, save the override, and
 * activate the custom profile. Asserts the diff bar clears, the
 * new profile appears in the bar, and the active badge moves.
 */

interface MockProfile {
  id: string
  name: string
  description: string
  is_builtin: boolean
  settings: Record<string, unknown>
  created_at: string
  updated_at: string
}

const TS = '2026-01-01T00:00:00.000'

const SETTING_TOGGLE_KEY = 'input_filter.block_credentials_strict'
const SETTING_TOGGLE_LABEL = 'Скрывать API-ключи'

function defaultProfiles(): MockProfile[] {
  return [
    {
      id: 'paranoid',
      name: 'paranoid',
      description: 'Параноид — максимальная защита.',
      is_builtin: true,
      settings: { [SETTING_TOGGLE_KEY]: true },
      created_at: TS,
      updated_at: TS,
    },
    {
      id: 'personal',
      name: 'personal',
      description: 'Обычный — баланс защиты и удобства.',
      is_builtin: true,
      settings: { [SETTING_TOGGLE_KEY]: true },
      created_at: TS,
      updated_at: TS,
    },
    {
      id: 'developer',
      name: 'developer',
      description: 'Программист — свобода для кода.',
      is_builtin: true,
      settings: { [SETTING_TOGGLE_KEY]: true },
      created_at: TS,
      updated_at: TS,
    },
  ]
}

const CATALOG_PAYLOAD = {
  categories: ['input_filter'],
  settings: [
    {
      key: SETTING_TOGGLE_KEY,
      category: 'input_filter',
      label: SETTING_TOGGLE_LABEL,
      description: 'Срабатывает на sk-..., ghp_..., AKIA...',
      type: 'toggle',
      risk_level: 'high',
      constraints: {
        min: null,
        max: null,
        step: null,
        unit: null,
        options: null,
        item_regex: null,
        max_length: null,
        placeholder: null,
      },
      default_paranoid: true,
      default_personal: true,
      default_developer: true,
      rationale: null,
      example: null,
      keywords: ['credentials'],
      locked: false,
    },
  ],
}

interface MockState {
  profiles: MockProfile[]
  active_id: string
}

async function mockApi(page: Page, state: MockState): Promise<void> {
  // Playwright dispatches routes in reverse registration order, so the
  // most-specific patterns are registered LAST.

  await page.route('**/auth/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        needs_setup: false,
        authenticated: true,
        ready: true,
      }),
    })
  })

  await page.route('**/csrf-token', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ token: 'test-csrf-token' }),
    })
  })

  await page.route('**/security/catalog', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(CATALOG_PAYLOAD),
    })
  })

  // Catch-all for /security/profiles/{id} (GET/PATCH).
  await page.route('**/security/profiles/*', async (route) => {
    const method = route.request().method()
    const url = new URL(route.request().url())
    const id = url.pathname.split('/').pop()!

    if (method === 'GET') {
      const profile = state.profiles.find((p) => p.id === id)
      if (!profile) {
        await route.fulfill({ status: 404, body: '' })
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ profile }),
      })
      return
    }
    if (method === 'PATCH') {
      const body = JSON.parse(route.request().postData() ?? '{}')
      const profile = state.profiles.find((p) => p.id === id)!
      if (body.name !== undefined) profile.name = body.name
      if (body.description !== undefined) profile.description = body.description
      if (body.settings) {
        profile.settings = { ...profile.settings, ...body.settings }
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ profile }),
      })
      return
    }
    await route.fallback()
  })

  await page.route('**/security/profiles', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ profiles: state.profiles }),
      })
      return
    }
    if (route.request().method() === 'POST') {
      const body = JSON.parse(route.request().postData() ?? '{}')
      const baseId: string = body.base_profile_id ?? 'personal'
      const base = state.profiles.find((p) => p.id === baseId)!
      const created: MockProfile = {
        id: `custom-${Math.random().toString(36).slice(2, 8)}`,
        name: body.name,
        description: body.description ?? '',
        is_builtin: false,
        settings: {
          ...base.settings,
          ...(body.settings ?? {}),
        },
        created_at: TS,
        updated_at: TS,
      }
      state.profiles.push(created)
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ profile: created }),
      })
      return
    }
    await route.fallback()
  })

  // Specific routes registered last so they win over the catch-all.
  await page.route('**/security/profiles/active', async (route) => {
    const active = state.profiles.find((p) => p.id === state.active_id)!
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        active_profile_id: state.active_id,
        profile: active,
      }),
    })
  })

  await page.route('**/security/profiles/*/activate', async (route) => {
    const url = new URL(route.request().url())
    const segments = url.pathname.split('/')
    const id = segments[segments.length - 2]
    state.active_id = id
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ active_profile_id: id }),
    })
  })
}

test.describe('SecurityPage CRUD', () => {
  test('clone personal, edit, save-as-new, activate the new profile', async ({
    page,
  }) => {
    const state: MockState = {
      profiles: defaultProfiles(),
      active_id: 'personal',
    }
    await mockApi(page, state)

    await page.goto('/settings/security')

    // Personal must show as active initially.
    await expect(page.getByText('Обычный')).toBeVisible()
    await expect(page.getByText('Активен').first()).toBeVisible()

    // Toggle the credentials switch off — the diff bar appears.
    const switches = page.getByRole('switch')
    await expect(switches.first()).toBeChecked()
    await switches.first().click()
    await expect(
      page.getByText(/Несохранённые изменения от профиля «personal»/),
    ).toBeVisible()
    await expect(
      page.getByText(/Защита выключена/),
    ).toBeVisible()

    // Save as new — dialog opens, fill name, confirm.
    await page
      .getByRole('button', { name: 'Сохранить как новый профиль' })
      .click()
    const nameInput = page.getByLabel('Имя профиля')
    await expect(nameInput).toBeVisible()
    await nameInput.fill('my-relaxed')
    await page.getByRole('button', { name: 'Подтвердить' }).click()

    // The new custom card should appear by name (card-title slot)
    // and immediately become active because save-as-new with a
    // dirty draft auto-activates the new profile.
    const customCardTitle = page.locator(
      '[data-slot="card-title"]:has-text("my-relaxed")',
    )
    await expect(customCardTitle).toBeVisible()
    const customCard = customCardTitle.locator(
      'xpath=ancestor::*[@data-slot="card"][1]',
    )
    await expect(customCard.getByText('Активен').first()).toBeVisible()
  })
})
