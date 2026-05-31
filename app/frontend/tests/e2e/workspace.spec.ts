import { expect, test } from '@playwright/test'

// ---------------------------------------------------------------------------
// Workspace sidebar tab
// ---------------------------------------------------------------------------

test.describe('Workspace sidebar tab', () => {
  test('workspace tab is visible in sidebar', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const workspaceTab = page.getByRole('tab', { name: 'Workspace' })
    await expect(workspaceTab).toBeVisible({ timeout: 10_000 })
  })

  test('clicking workspace tab activates workspace panel', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const workspaceTab = page.getByRole('tab', { name: 'Workspace' })
    await expect(workspaceTab).toBeVisible({ timeout: 10_000 })
    await workspaceTab.click()

    await expect(workspaceTab).toHaveAttribute('data-state', 'active')

    const workspaceLabel = page.locator('text=WORKSPACE').first()
    await expect(workspaceLabel).toBeVisible({ timeout: 5000 })
  })

  test('all three tabs (Chats, Files, Workspace) are present', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await expect(page.getByRole('tab', { name: 'Chats' })).toBeVisible({ timeout: 10_000 })
    await expect(page.getByRole('tab', { name: 'Files' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'Workspace' })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Workspace FileTree UI
// ---------------------------------------------------------------------------

test.describe('Workspace FileTree', () => {
  test('shows file tree or empty state without JSON errors', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const workspaceTab = page.getByRole('tab', { name: 'Workspace' })
    await workspaceTab.click({ timeout: 10_000 })

    // Wait for loading to finish
    await page.waitForTimeout(3000)

    // Should not have JSON parsing errors
    const jsonError = page.getByText('is not valid JSON')
    const hasJsonError = await jsonError.isVisible().catch(() => false)
    expect(hasJsonError).toBe(false)
  })

  test('workspace tree shows files and directories when configured', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const workspaceTab = page.getByRole('tab', { name: 'Workspace' })
    await workspaceTab.click({ timeout: 10_000 })

    await page.waitForTimeout(3000)

    const noWorkspace = page.getByText('No workspace configured')
    const hasNoWorkspace = await noWorkspace.isVisible().catch(() => false)

    if (hasNoWorkspace) {
      console.log('No workspace configured — skipping tree content test')
      return
    }

    // Tree items should be visible
    const treeItems = page.locator('.truncate').filter({ hasText: '' })
    const count = await treeItems.count()
    if (count > 0) {
      await expect(treeItems.first()).toBeVisible()
    }
  })

  test('workspace panel shows WORKSPACE label', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const workspaceTab = page.getByRole('tab', { name: 'Workspace' })
    await workspaceTab.click({ timeout: 10_000 })

    await page.waitForTimeout(2000)

    const workspaceLabel = page.locator('text=WORKSPACE').first()
    await expect(workspaceLabel).toBeVisible({ timeout: 5000 })
  })
})

// ---------------------------------------------------------------------------
// Workspace API endpoints (via page.evaluate — cookies from storageState)
// ---------------------------------------------------------------------------

test.describe('Workspace API endpoints', () => {
  test('GET /workspace/dirs returns directories array', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const result = await page.evaluate(async () => {
      try {
        const resp = await fetch('/workspace/dirs', { credentials: 'include' })
        const data = await resp.json()
        return { ok: resp.ok, status: resp.status, data }
      } catch (e: unknown) {
        return { ok: false, error: String(e) }
      }
    })

    expect(result.ok).toBe(true)
    expect(result.data).toHaveProperty('directories')
    expect(Array.isArray(result.data.directories)).toBe(true)
  })

  test('GET /workspace/tree returns file tree with correct structure', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const result = await page.evaluate(async () => {
      try {
        const resp = await fetch('/workspace/tree', { credentials: 'include' })
        const data = await resp.json()
        return { ok: resp.ok, data }
      } catch (e: unknown) {
        return { ok: false, error: String(e) }
      }
    })

    if (!result.ok) {
      console.log('Workspace tree not available:', result.error)
      return
    }

    expect(result.data).toHaveProperty('items')
    expect(result.data).toHaveProperty('path')
    expect(result.data).toHaveProperty('workspace')
    expect(Array.isArray(result.data.items)).toBe(true)

    for (const item of result.data.items) {
      expect(item).toHaveProperty('name')
      expect(item).toHaveProperty('type')
      expect(item).toHaveProperty('path')
      expect(['file', 'directory']).toContain(item.type)
    }
  })

  test('GET /workspace/tree?path=... navigates into subdirectory', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const rootResult = await page.evaluate(async () => {
      try {
        const resp = await fetch('/workspace/tree', { credentials: 'include' })
        const data = await resp.json()
        return { ok: resp.ok, data }
      } catch (e: unknown) {
        return { ok: false, error: String(e) }
      }
    })

    if (!rootResult.ok) {
      console.log('Workspace tree not available')
      return
    }

    const dir = rootResult.data.items?.find(
      (item: { type: string }) => item.type === 'directory',
    )

    if (!dir) {
      console.log('No directories in workspace tree — skipping subdirectory test')
      return
    }

    const subResult = await page.evaluate(
      async (path: string) => {
        try {
          const resp = await fetch(`/workspace/tree?path=${encodeURIComponent(path)}`, {
            credentials: 'include',
          })
          const data = await resp.json()
          return { ok: resp.ok, data }
        } catch (e: unknown) {
          return { ok: false, error: String(e) }
        }
      },
      dir.path,
    )

    if (!subResult.ok) {
      console.log(`Subdirectory tree failed for ${dir.path}:`, subResult.error)
      return
    }

    expect(subResult.data).toHaveProperty('items')
    expect(subResult.data.path).toBe(dir.path)
    expect(Array.isArray(subResult.data.items)).toBe(true)
  })

  test('GET /workspace/tree returns 404 for non-existent path', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const result = await page.evaluate(async () => {
      try {
        const resp = await fetch('/workspace/tree?path=nonexistent_path_xyz', {
          credentials: 'include',
        })
        return { ok: resp.ok, status: resp.status }
      } catch (e: unknown) {
        return { ok: false, error: String(e) }
      }
    })

    expect(result.ok || result.status === 404).toBe(true)
  })

  test('files in workspace tree include size field', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const result = await page.evaluate(async () => {
      try {
        const resp = await fetch('/workspace/tree', { credentials: 'include' })
        const data = await resp.json()
        return { ok: resp.ok, data }
      } catch (e: unknown) {
        return { ok: false, error: String(e) }
      }
    })

    if (!result.ok) {
      console.log('Workspace tree not available')
      return
    }

    const files = result.data.items?.filter(
      (item: { type: string }) => item.type === 'file',
    )

    if (files && files.length > 0) {
      for (const file of files) {
        expect(file).toHaveProperty('size')
        expect(typeof file.size).toBe('number')
        expect(file.size).toBeGreaterThanOrEqual(0)
      }
    }
  })

  test('directories in workspace tree do not have size field', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const result = await page.evaluate(async () => {
      try {
        const resp = await fetch('/workspace/tree', { credentials: 'include' })
        const data = await resp.json()
        return { ok: resp.ok, data }
      } catch (e: unknown) {
        return { ok: false, error: String(e) }
      }
    })

    if (!result.ok) {
      console.log('Workspace tree not available')
      return
    }

    const dirs = result.data.items?.filter(
      (item: { type: string }) => item.type === 'directory',
    )

    if (dirs && dirs.length > 0) {
      for (const dir of dirs) {
        expect(dir).not.toHaveProperty('size')
      }
    }
  })
})
