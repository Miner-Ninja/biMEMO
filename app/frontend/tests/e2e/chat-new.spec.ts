import { expect, test } from "@playwright/test";

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "https://localhost";
const PASSWORD = process.env.TEST_PASSWORD || "testpass123";

// No LLM involved: creating/switching/deleting conversations only touches
// the REST conversation endpoints.

async function login(page: import("@playwright/test").Page) {
  const chatInput = page.locator("textarea").first();
  const passwordInput = page.getByLabel("Password");

  await page.goto(`${BASE_URL}/`);

  if (await chatInput.isVisible().catch(() => false)) {
    return;
  }

  // The app may paint the login screen while its session check is still in
  // flight; both waits are generous because the same slowness applies to
  // the post-login full load (chat UI) under host load.
  await expect(passwordInput).toBeVisible({ timeout: 60_000 });
  await passwordInput.fill(PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(chatInput).toBeVisible({ timeout: 90_000 });
}

// `previous` must be captured BEFORE the triggering click: a switch is an
// instant client-side navigation, so the URL may already have changed by
// the time the wait starts (waitForFunction then resolves immediately).
async function waitUrlChange(page: import("@playwright/test").Page, previous: string) {
  await page.waitForFunction((prev) => window.location.href !== prev, previous, {
    timeout: 30_000,
  });
}

test.describe("new chat lifecycle", () => {
  test("create a chat, see it in the list, switch chats, delete it @smoke", async ({
    page,
  }) => {
    test.setTimeout(180_000);

    await login(page);
    // The sidebar list loads from GET /chat/conversations after the
    // (re)navigation; wait for that response so the count below is not a
    // race against the list fetch.
    const listLoaded = page.waitForResponse(
      (r) =>
        r.request().method() === "GET" &&
        r.url().includes("/chat/conversations") &&
        !r.url().includes("/messages"),
      { timeout: 120_000 }
    );
    await page.goto(`${BASE_URL}/`);
    await listLoaded;

    // The sidebar "New Chat" button is the only real <button> with that
    // text (list entries are div[role=button]).
    const newChatButton = page.locator('button:has-text("New Chat")');
    await expect(newChatButton).toBeVisible({ timeout: 30_000 });

    // Chat list entries: the item root is div[role="button"] holding the
    // title span; the user-menu trigger is a real <button>, so this
    // selector only matches list entries.
    const items = page.locator('div[role="button"]:has(span.truncate)');
    await expect(items.first()).toBeVisible({ timeout: 30_000 });
    const beforeCount = await items.count();
    expect(
      beforeCount,
      "expected at least one pre-existing conversation"
    ).toBeGreaterThanOrEqual(1);

    // 1. Create: the new conversation lands first in the list (most
    //    recently updated) and the URL points at it.
    const urlBefore = page.url();
    await newChatButton.click();
    await waitUrlChange(page, urlBefore);
    const match = page.url().match(/\/c\/([0-9a-f-]{36})\/?$/);
    expect(match, `expected /c/<id> URL, got ${page.url()}`).not.toBeNull();
    const newCid = match![1];

    await expect(items).toHaveCount(beforeCount + 1, { timeout: 30_000 });
    await expect(items.first()).toHaveText("New Chat");

    // 2. Switch to another conversation and back to the new one.
    const other = items.nth(1);
    const beforeSwitch = page.url();
    await other.click();
    await waitUrlChange(page, beforeSwitch);
    expect(page.url()).not.toContain(newCid);

    const beforeBack = page.url();
    await items.first().click();
    await waitUrlChange(page, beforeBack);
    expect(page.url()).toContain(newCid);

    // 3. Delete: hover the entry, open its actions menu, pick Delete.
    //    The user-visible contract: the delete succeeds (204). A first 403
    //    (stale CSRF token) is tolerated: the client (bimemo-api.ts)
    //    re-fetches the token and retries automatically, and the user sees
    //    the retry's outcome. Any other final status fails the test.
    const firstItem = items.first();
    await firstItem.hover();
    const deleteOf = (res: { request(): { method(): string; url(): string } }) =>
      res.request().method() === "DELETE" &&
      res.url().includes(`/chat/conversations/${newCid}`);
    await firstItem.getByLabel("Chat actions").click();
    await page.getByRole("menuitem", { name: "Delete" }).click();
    let response = await page.waitForResponse(deleteOf, { timeout: 60_000 });
    if (response.status() === 403) {
      // Stale CSRF token: the client retried with a fresh one; wait for
      // the retry's answer (the one the user ends up seeing).
      response = await page.waitForResponse(deleteOf, { timeout: 60_000 });
    }
    expect(response.status()).toBe(204);

    await expect(items).toHaveCount(beforeCount, { timeout: 30_000 });
    expect(page.url()).not.toContain(newCid);
  });
});
