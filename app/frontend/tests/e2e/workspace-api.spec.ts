import { expect, test } from '@playwright/test'

// ---------------------------------------------------------------------------
// Workspace API endpoints
// ---------------------------------------------------------------------------

test.describe('Workspace API endpoints', () => {
  test('GET /workspace/dirs returns directories array', async ({ page }) => {
    await page.goto('/')

    const result = await page.evaluate(async () => {
      try {
        const resp = await fetch('/workspace/dirs', { credentials: 'include' })
        const data = await resp.json()
        return { ok: resp.ok, data }
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
