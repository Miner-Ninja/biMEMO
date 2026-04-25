import { expect, test, type Page } from '@playwright/test'

/**
 * Smoke probe for /settings/security.
 *
 * Mocks the auth status and the four /security/* endpoints the
 * page depends on (catalog, profiles, active, plus the GET-by-id
 * call shape) and asserts that:
 *   - the page header and search input render
 *   - the three built-in profile cards appear by their Russian
 *     display labels (Параноид / Обычный / Программист)
 *   - the editable category accordion lists at least two
 *     categories from the mocked catalog
 *   - the Always-On lock section surfaces a locked entry
 */

interface MockSetting {
  key: string
  category: string
  label: string
  description: string
  type: string
  risk_level: string
  constraints: Record<string, unknown>
  default_paranoid: unknown
  default_personal: unknown
  default_developer: unknown
  rationale: string | null
  example: string | null
  keywords: string[]
  locked: boolean
}

const MOCK_SETTINGS: MockSetting[] = [
  {
    key: 'input_filter.block_prompt_injection',
    category: 'input_filter',
    label: 'Блокировать попытки джейлбрейка LLM',
    description: 'Останавливает обработку при попытках обмана модели.',
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
    keywords: ['jailbreak', 'инъекция'],
    locked: false,
  },
  {
    key: 'agent_limits.iterations.max',
    category: 'agent_limits',
    label: 'Максимум шагов в одном задании',
    description: 'Сколько раз агент может думать-выполнять за один запрос.',
    type: 'slider_int',
    risk_level: 'low',
    constraints: {
      min: 3,
      max: 500,
      step: 1,
      unit: 'шагов',
      options: null,
      item_regex: null,
      max_length: null,
      placeholder: null,
    },
    default_paranoid: 10,
    default_personal: 20,
    default_developer: 80,
    rationale: null,
    example: null,
    keywords: ['iterations'],
    locked: false,
  },
  {
    key: 'network.block_cloud_metadata',
    category: 'network',
    label: 'Блокировать cloud metadata IP (169.254.169.254)',
    description:
      'Адрес 169.254.169.254 хранит токены доступа в облаке. biMEMO никогда не будет к нему обращаться.',
    type: 'toggle',
    risk_level: 'critical',
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
    keywords: ['metadata', 'cloud'],
    locked: true,
  },
]

const MOCK_CATEGORIES = ['input_filter', 'agent_limits', 'network']

const MOCK_PROFILES = [
  {
    id: 'paranoid',
    name: 'paranoid',
    description: 'Параноид — максимальная защита.',
    is_builtin: true,
    settings: {
      'input_filter.block_prompt_injection': true,
      'agent_limits.iterations.max': 10,
    },
    created_at: '2026-01-01T00:00:00.000',
    updated_at: '2026-01-01T00:00:00.000',
  },
  {
    id: 'personal',
    name: 'personal',
    description: 'Обычный — баланс защиты и удобства.',
    is_builtin: true,
    settings: {
      'input_filter.block_prompt_injection': true,
      'agent_limits.iterations.max': 20,
    },
    created_at: '2026-01-01T00:00:00.000',
    updated_at: '2026-01-01T00:00:00.000',
  },
  {
    id: 'developer',
    name: 'developer',
    description: 'Программист — свобода для кода.',
    is_builtin: true,
    settings: {
      'input_filter.block_prompt_injection': true,
      'agent_limits.iterations.max': 80,
    },
    created_at: '2026-01-01T00:00:00.000',
    updated_at: '2026-01-01T00:00:00.000',
  },
]

async function mockSecurityRoutes(page: Page): Promise<void> {
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

  await page.route('**/security/catalog', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        categories: MOCK_CATEGORIES,
        settings: MOCK_SETTINGS,
      }),
    })
  })

  await page.route('**/security/profiles', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ profiles: MOCK_PROFILES }),
      })
      return
    }
    await route.fallback()
  })

  await page.route('**/security/profiles/active', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        active_profile_id: 'personal',
        profile: MOCK_PROFILES[1],
      }),
    })
  })
}

test.describe('SecurityPage smoke', () => {
  test.beforeEach(async ({ page }) => {
    await mockSecurityRoutes(page)
  })

  test('renders header, profile cards, categories, and locked entry', async ({
    page,
  }) => {
    await page.goto('/settings/security')

    // Header
    await expect(
      page.getByRole('heading', { name: 'Безопасность' }),
    ).toBeVisible()
    await expect(
      page.getByPlaceholder('Найти настройку…'),
    ).toBeVisible()

    // Three built-in profile cards (display labels from i18n/ru.ts)
    await expect(page.getByText('Параноид', { exact: true })).toBeVisible()
    await expect(page.getByText('Обычный', { exact: true })).toBeVisible()
    await expect(page.getByText('Программист', { exact: true })).toBeVisible()

    // Active badge on the personal card
    await expect(page.getByText('Активен').first()).toBeVisible()

    // Editable categories — at least two of the three mocked
    await expect(
      page.getByText('Что вы пишете в biMEMO'),
    ).toBeVisible()
    await expect(
      page.getByText('Лимиты на работу агента'),
    ).toBeVisible()

    // Locked section heading + locked entry label
    await expect(page.getByText('Always On (нельзя отключить)')).toBeVisible()
    await expect(
      page.getByText('Блокировать cloud metadata IP (169.254.169.254)'),
    ).toBeVisible()
  })
})
