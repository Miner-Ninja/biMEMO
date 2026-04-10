import { expect, test } from '@playwright/test'

test.describe('auth entry smoke', () => {
  test('shows login screen when setup is complete but user is not authenticated', async ({ page }) => {
    await page.route('**/auth/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          needs_setup: false,
          authenticated: false,
        }),
      })
    })

    await page.goto('/')

    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole('heading', { name: 'biMEMO' })).toBeVisible()
    await expect(page.getByLabel('Password')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Sign In' })).toBeVisible()
  })

  test('enables sign in after entering a password on the login screen', async ({ page }) => {
    await page.route('**/auth/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          needs_setup: false,
          authenticated: false,
        }),
      })
    })

    await page.goto('/')

    const passwordInput = page.getByLabel('Password')
    const signInButton = page.getByRole('button', { name: 'Sign In' })

    await expect(signInButton).toBeDisabled()
    await passwordInput.fill('test-password')
    await expect(signInButton).toBeEnabled()
  })
})
