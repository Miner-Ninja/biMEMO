import { expect, test } from "@playwright/test";
import type { BrowserContext, Page } from "@playwright/test";

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "https://localhost";
const PASSWORD = process.env.TEST_PASSWORD || "testpass123";
const WRONG_PASSWORD = "definitely-wrong-password-9x";

// All scenarios run in isolated browser contexts created here — the shared
// tests/e2e/.auth/state.json is never used or written by this spec.
// storageState must be passed explicitly (as an empty state): the test
// runner otherwise merges the config's `use.storageState` into every
// context created during a test, which would pre-authenticate them.
const EMPTY_STATE = { cookies: [], origins: [] };

// The one shared context (worker-scoped, serial describe) carries the
// session across the login → logout → re-login scenarios.
type Isolated = { context: BrowserContext; page: Page };

let shared: Isolated | null = null;

async function expectChatUi(page: Page, timeout = 90_000): Promise<void> {
  await expect(page.locator("textarea").first()).toBeVisible({ timeout });
  const status = await (await page.request.get("/auth/status")).json();
  expect(status).toEqual(expect.objectContaining({ authenticated: true }));
}

test.describe("auth flow", () => {
  // Serial: the scenarios share one session. 120s timeout: the shared
  // context reloads the app on every scenario and, under host load, the
  // sidebar (which holds the user menu) can take longer than the default
  // 30s to appear.
  test.describe.configure({ mode: "serial", timeout: 120_000 });
  test.beforeAll(async ({ browser }) => {
    const context = await browser.newContext({
      ignoreHTTPSErrors: true,
      storageState: EMPTY_STATE,
    });
    const page = await context.newPage();
    shared = { context, page };
  });

  test.afterAll(async () => {
    await shared?.context.close();
    shared = null;
  });

  test("login with the right password reaches the chat UI @smoke", async () => {
    const { page } = shared!;
    await page.goto(`${BASE_URL}/`);
    await expect(page).toHaveURL(/\/login$/, { timeout: 30_000 });
    const passwordInput = page.getByLabel("Password");
    await expect(passwordInput).toBeVisible({ timeout: 60_000 });
    await passwordInput.fill(PASSWORD);
    await page.getByRole("button", { name: "Sign In" }).click();
    await expectChatUi(page);
  });

  test("wrong password shows an error and creates no session @smoke", async ({
    browser,
  }) => {
    // A separate fresh context: never logged in, no shared state.
    const context = await browser.newContext({
      ignoreHTTPSErrors: true,
      storageState: EMPTY_STATE,
    });
    const page = await context.newPage();
    try {
      const loginResponse = page.waitForResponse(
        (res) => res.url().includes("/auth/login") && res.request().method() === "POST"
      );
      await page.goto(`${BASE_URL}/`);
      await expect(page.getByLabel("Password")).toBeVisible({ timeout: 30_000 });
      await page.getByLabel("Password").fill(WRONG_PASSWORD);
      await page.getByRole("button", { name: "Sign In" }).click();

      const response = await loginResponse;
      // 401 = rejected password; 429 = the brute-force limiter triggered by
      // earlier failures (still a correct rejection, surfaced as a UI error).
      expect([401, 429]).toContain(response.status());
      // Assert on the exact message text, not on a class: the app shows
      // other .text-destructive elements too (e.g. an error status badge),
      // which would trip strict mode.
      if (response.status() === 401) {
        await expect(page.getByText("Invalid password")).toBeVisible({
          timeout: 10_000,
        });
      } else {
        await expect(page.getByText(/Too many attempts/)).toBeVisible({
          timeout: 10_000,
        });
      }
      // The failed login must not have issued a session: the context
      // (which would hold any Set-Cookie from the attempt) is anonymous.
      const status = await (await context.request.get("/auth/status")).json();
      expect(status).toEqual(
        expect.objectContaining({ authenticated: false, needs_setup: false })
      );
      // Still on the login screen.
      await expect(page.getByLabel("Password")).toBeVisible();
    } finally {
      await context.close();
    }
  });

  test("logout returns to the login screen @smoke", async () => {
    const { page } = shared!;
    // Still authenticated from the first scenario (same context, chat UI
    // already rendered) — no reload here: a full reload is the slowest step
    // under host load and would only make this test flaky.
    await expectChatUi(page);

    // The user-menu trigger varies between UI builds: the older one is an
    // avatar-image button, the current one is the name row at the bottom
    // of the sidebar. Click whichever is present.
    const avatarMenu = page
      .locator("button")
      .filter({ has: page.getByAltText("User avatar") });
    const nameMenu = page.locator("aside button").filter({ hasText: "User" });
    let userMenu = avatarMenu;
    for (let i = 0; i < 20 && (await avatarMenu.count()) === 0; i++) {
      await page.waitForTimeout(500);
    }
    if ((await avatarMenu.count()) === 0) {
      await expect(nameMenu.first()).toBeVisible({ timeout: 90_000 });
      userMenu = nameMenu;
    }
    await userMenu.first().click();
    // The popup is portaled; address the Sign out button directly.
    await page.getByRole("button", { name: "Sign out" }).first().click();

    await expect(page).toHaveURL(/\/login$/, { timeout: 30_000 });
    await expect(page.getByLabel("Password")).toBeVisible({ timeout: 60_000 });
    const status = await (await page.request.get("/auth/status")).json();
    expect(status).toEqual(expect.objectContaining({ authenticated: false }));
  });

  test("re-login after logout works @smoke", async () => {
    const { page } = shared!;
    // The logout test left the page on the login screen — no reload here
    // (a full reload is the slowest step under host load).
    await expect(page.getByLabel("Password")).toBeVisible({ timeout: 60_000 });
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign In" }).click();
    await expectChatUi(page);
  });
});
