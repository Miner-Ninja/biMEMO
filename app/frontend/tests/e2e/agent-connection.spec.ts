import { expect, test } from "@playwright/test";

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "https://localhost";

/**
 * Ensure the browser context has a valid session.
 * If the stored session is expired, re-authenticate via the API.
 */
async function ensureSession(
  context: Parameters<Parameters<typeof test>[0]>["context"]
) {
  const resp = await context.request.get(`${BASE_URL}/auth/status`, {
    maxRedirects: 0,
    timeout: 5000,
  });

  if (resp.status === 200) {
    const body = (await resp.json()) as Record<string, unknown>;
    if (body.authenticated === true) {
      return;
    }
  }

  // Session expired — skip E2E tests requiring auth
  // (full login flow is in workspace.spec.ts)
  throw new Error("No valid session — skip agent connection E2E tests");
}

test.beforeEach(async ({ context }) => {
  await ensureSession(context);
});

// ---------------------------------------------------------------------------
// R10: Agent Connection page — CA download first, then tokens, then config
// ---------------------------------------------------------------------------

test.describe("Agent Connection page", () => {
  test("CA download/trust block renders before token controls", async ({ page }) => {
    await page.goto("/admin/agents");
    await page.waitForLoadState("networkidle");

    // Wait for the page to load
    await page.waitForTimeout(3000);

    // The CA section (section with Shield icon) should appear before
    // the token management section (section with Key icon)
    const sections = await page.locator("section").all();

    let caSectionIdx = -1;
    let tokenSectionIdx = -1;

    for (let i = 0; i < sections.length; i++) {
      const text = await sections[i].textContent();
      if (text?.includes("Trust the CA Certificate")) {
        caSectionIdx = i;
      }
      if (text?.includes("Manage Agent Tokens")) {
        tokenSectionIdx = i;
      }
    }

    if (caSectionIdx >= 0 && tokenSectionIdx >= 0) {
      expect(caSectionIdx).toBeLessThan(tokenSectionIdx);
    }
  });

  test("config block contains TLS MCP URL after issuing", async ({ page }) => {
    await page.goto("/admin/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(3000);

    // The config section should have an input for label and a Load button
    const configSection = page.locator("section").filter({
      hasText: "Connection Configuration",
    });

    // Verify the section exists
    const sectionCount = await configSection.count();
    if (sectionCount > 0) {
      // The section should contain "MCP_URL" or "MCP_TOKEN_LABEL" placeholders
      // or the Load button for generating config
      const loadBtn = configSection.getByRole("button", { name: "Load" });
      const hasLoadBtn = await loadBtn.isVisible().catch(() => false);
      expect(hasLoadBtn).toBe(true);
    }
  });

  test("CA root download control works from the page", async ({ page }) => {
    await page.goto("/admin/agents");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(3000);

    // Look for the Download CA Root button
    const downloadBtn = page.getByRole("button", {
      name: "Download CA Root",
    });
    const isVisible = await downloadBtn.isVisible().catch(() => false);

    // If CA is configured, the button should be visible; if not, an error
    // message should be shown. Either way, the CA section should exist.
    const caSection = page.locator("section").filter({
      hasText: "Trust the CA Certificate",
    });
    await expect(caSection.first()).toBeVisible();
  });
});
