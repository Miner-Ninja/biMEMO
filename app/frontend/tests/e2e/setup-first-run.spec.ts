import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// @deep + MANUAL GATE.
//
// This scenario requires a stack whose password is NOT set yet — i.e. a
// FRESH encrypted key file + database. On the live stack the first step
// would REPLACE the user's password and re-create the database, destroying
// the user's data. Therefore it is skipped unless E2E_SETUP_FIRST_RUN=1,
// and must only be pointed at a disposable stack. See tests/e2e/README.md
// for how to build one (separate compose project name + throwaway volumes).

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "https://localhost";
const SETUP_PASSWORD = process.env.E2E_SETUP_PASSWORD || "setuppass123";
const ALLOWED = process.env.E2E_SETUP_FIRST_RUN === "1";

const MANUAL_NOTE =
  "manual-only: requires a DISPOSABLE stack with an unset password " +
  "(separate compose project name + throwaway volumes — see tests/e2e/README.md). " +
  "Never run against the live stack. Set E2E_SETUP_FIRST_RUN=1 to enable.";

async function signOutViaUserMenu(page: Page) {
  const userMenu = page
    .locator("button")
    .filter({ has: page.getByAltText("User avatar") });
  await userMenu.click();
  const popup = page.locator('[data-slot="user-menu-popup"]');
  await expect(popup).toBeVisible();
  await popup.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/, { timeout: 30_000 });
}

test.describe("setup first run", () => {
  test.setTimeout(300_000);

  test("first launch: set the password, then log in @deep", async ({ browser }) => {
    test.skip(!ALLOWED, MANUAL_NOTE);

    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await context.newPage();
    try {
      // 1. Fresh stack: the app routes the unauthenticated user to /setup.
      await page.goto(`${BASE_URL}/`);
      await expect(page).toHaveURL(/\/setup$/, { timeout: 30_000 });

      // 2. Choose the password (min 8 chars, confirmed) and submit.
      const passwordInput = page.getByLabel("Password");
      const confirmInput = page.getByLabel("Confirm Password");
      await expect(passwordInput).toBeVisible();
      await passwordInput.fill(SETUP_PASSWORD);
      await confirmInput.fill(SETUP_PASSWORD);
      const createButton = page.getByRole("button", { name: "Create Password" });
      await expect(createButton).toBeEnabled();
      await createButton.click();

      // The app (re)starts its encrypted services after setup; the chat
      // UI appears once the application is ready again.
      await expect(page.locator("textarea").first()).toBeVisible({
        timeout: 120_000,
      });

      // 3. The chosen password is the normal login password now:
      //    log out, then log back in through the regular login screen.
      await signOutViaUserMenu(page);
      await expect(page.getByLabel("Password")).toBeVisible();
      await page.getByLabel("Password").fill(SETUP_PASSWORD);
      await page.getByRole("button", { name: "Sign In" }).click();
      await expect(page.locator("textarea").first()).toBeVisible({
        timeout: 120_000,
      });

      // 4. The instance no longer needs setup and the session is valid.
      const status = (await page.request.get("/auth/status")).json();
      expect(status).toEqual(
        expect.objectContaining({ needs_setup: false, authenticated: true })
      );
    } finally {
      await context.close();
    }
  });
});
