import { expect, test } from "@playwright/test";

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:4173";
const PASSWORD = process.env.TEST_PASSWORD || "testpass123";
const REQUEST_START_TIMEOUT = 15_000;
const RESPONSE_TIMEOUT = 180_000;

async function login(page: import("@playwright/test").Page) {
  const input = page.locator("textarea").first();
  const passwordInput = page.getByLabel("Password");

  await page.goto(`${BASE_URL}/login`);

  if (await input.isVisible().catch(() => false)) {
    return;
  }

  await expect(passwordInput).toBeVisible({ timeout: 30_000 });
  await passwordInput.fill(PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(input).toBeVisible({ timeout: 30_000 });
}

async function createNewChat(page: import("@playwright/test").Page) {
  const newChatButton = page
    .locator('button[aria-label="New chat"], button:has-text("New Chat")')
    .first();
  if (await newChatButton.isVisible()) {
    await newChatButton.click();
  }
  await expect(page.locator("textarea").first()).toBeVisible({ timeout: 30_000 });
}

test.describe("Chat realtime recovery", () => {
  test.setTimeout(240_000);

  test("reload during generation recovers final answer and unlocks input", async ({
    page,
  }) => {
    await login(page);
    await createNewChat(page);

    const input = page.locator("textarea").first();
    const stopButton = page.getByLabel("Stop generation");
    const message = "какой курс биткойна, эфириума и соланы на данный момент?";

    await input.fill(message);
    await page.keyboard.press("Enter");
    await stopButton.waitFor({ state: "visible", timeout: REQUEST_START_TIMEOUT });

    await page.reload({ waitUntil: "domcontentloaded" });

    await expect(input).toBeVisible({ timeout: 30_000 });
    await expect(input).toBeEnabled({ timeout: RESPONSE_TIMEOUT });
    await expect(page.locator(".markdown-content").last()).toContainText(
      /bitcoin|битко|ethereum|эфир|solana|солан/i,
      {
        timeout: RESPONSE_TIMEOUT,
      }
    );
  });
});
