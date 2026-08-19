import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// @deep: requires a RUNNING vLLM endpoint (the turn must actually generate).
// NOT run by the smoke pass; see tests/e2e/README.md.

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "https://localhost";
const PASSWORD = process.env.TEST_PASSWORD || "testpass123";
const STORAGE_STATE = "tests/e2e/.auth/state.json";
const REQUEST_START_TIMEOUT = 15_000;
const RESPONSE_TIMEOUT = 120_000;
// A long-prompt turn that needs no tools and has a deterministic,
// checkable answer, so the final answer can be verified after recovery.
const PROMPT =
  "Write a history of the printing press in ten short paragraphs. " +
  "Finish with a list of exactly five notable early printed books, one per line.";

async function login(page: Page) {
  const chatInput = page.locator("textarea").first();
  const passwordInput = page.getByLabel("Password");

  await page.goto(`${BASE_URL}/login`);

  if (await chatInput.isVisible().catch(() => false)) {
    return;
  }

  await expect(passwordInput).toBeVisible({ timeout: 30_000 });
  await passwordInput.fill(PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(chatInput).toBeVisible({ timeout: 30_000 });
}

async function createNewChat(page: Page) {
  const newChatButton = page
    .locator('button[aria-label="New chat"], button:has-text("New Chat")')
    .first();
  if (await newChatButton.isVisible()) {
    await newChatButton.click();
  }
  await expect(page.locator("textarea").first()).toBeVisible({ timeout: 30_000 });
}

test.describe("WS reconnect (fast)", () => {
  test.setTimeout(180_000);

  test("drop the connection mid-generation, reconnect, final answer restored @deep", async ({
    page,
    context,
    browser,
  }) => {
    await login(page);
    await createNewChat(page);
    const match = page.url().match(/\/c\/([0-9a-f-]{36})\/?$/);
    expect(match, `expected /c/<id> URL, got ${page.url()}`).not.toBeNull();
    const conversationId = match![1];

    const input = page.locator("textarea").first();
    const stopButton = page.getByLabel("Stop generation");

    await input.fill(PROMPT);
    await page.keyboard.press("Enter");
    // The stream has started: the send button turns into Stop.
    await stopButton.waitFor({ state: "visible", timeout: REQUEST_START_TIMEOUT });
    // Give the generation a few seconds so it is genuinely in flight.
    await page.waitForTimeout(3_000);

    // Sever the WS: close the whole context (its sockets die with it).
    await context.close();

    // Reconnect: a NEW context carrying the same session (storageState),
    // opened on the same conversation.
    const fresh = await browser.newContext({
      storageState: STORAGE_STATE,
      ignoreHTTPSErrors: true,
    });
    const page2 = await fresh.newPage();
    try {
      await page2.goto(`${BASE_URL}/c/${conversationId}`);
      await expect(page2.locator("textarea").first()).toBeVisible({
        timeout: 30_000,
      });

      // The turn keeps running server-side; the reconnected client must
      // see the final answer and unlock the input.
      await expect(page2.locator(".markdown-content").last()).toContainText(/print/i, {
        timeout: RESPONSE_TIMEOUT,
      });
      await expect(page2.locator("textarea").first()).toBeEnabled({
        timeout: RESPONSE_TIMEOUT,
      });
      // The user's prompt must not be duplicated by the recovery.
      const userBubbles = page2.locator(".markdown-content").filter({
        hasText: PROMPT,
      });
      expect(await userBubbles.count()).toBeLessThanOrEqual(1);
    } finally {
      await fresh.close();
    }
  });
});
