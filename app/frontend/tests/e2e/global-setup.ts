import type { FullConfig } from "@playwright/test";

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "https://localhost";
const PASSWORD = process.env.TEST_PASSWORD || "testpass123";

async function globalSetup(_config: FullConfig) {
  const playwright = await import("playwright");
  const apiContext = await playwright.request.newContext({
    ignoreHTTPSErrors: true,
  });

  // Step 1: Wait for backend to be healthy
  console.log("Waiting for backend health...");
  let healthy = false;
  for (let i = 0; i < 30; i++) {
    try {
      const healthResp = await apiContext.get(`${BASE_URL}/health`);
      const healthData = (await healthResp.json()) as Record<string, unknown>;
      if (healthData.status === "ok") {
        healthy = true;
        break;
      }
    } catch {
      // ignore
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  if (!healthy) {
    throw new Error("Backend did not become healthy within 60s");
  }
  console.log("Backend is healthy");

  // Step 2: Check auth status
  const statusResp = await apiContext.get(`${BASE_URL}/auth/status`);
  const status = (await statusResp.json()) as Record<string, unknown>;
  console.log("Auth status:", JSON.stringify(status));

  // Step 3: Unlock if needed (startup gate)
  if (
    !status.authenticated &&
    (status.startup_state === "locked" || status.startup_state === "auto_unlock_failed")
  ) {
    console.log("Unlocking via startup gate...");
    const unlockResp = await apiContext.post(`${BASE_URL}/auth/login`, {
      data: { password: PASSWORD },
      headers: { "Content-Type": "application/json" },
    });
    console.log("Unlock response:", unlockResp.status(), await unlockResp.text());

    // Wait for backend to finish processing unlock and restart
    console.log("Waiting for backend to restart after unlock...");
    await new Promise((r) => setTimeout(r, 15000));

    // Wait for health again
    for (let i = 0; i < 30; i++) {
      try {
        const healthResp = await apiContext.get(`${BASE_URL}/health`);
        const healthData = (await healthResp.json()) as Record<string, unknown>;
        if (healthData.status === "ok") {
          break;
        }
      } catch {
        // ignore
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
  }

  // Step 4: Login via browser UI (gets session cookie)
  console.log("Launching browser for login...");
  const browser = await playwright.chromium.launch();
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
  });
  const page = await context.newPage();

  // Navigate to the app root
  await page.goto(`${BASE_URL}/`);
  await page.waitForTimeout(5000);

  // Check if we're on the login page
  const passwordInput = page.getByLabel("Password");
  const isLoginPage = await passwordInput.isVisible().catch(() => false);

  if (isLoginPage) {
    console.log("Login page detected, entering password...");
    await passwordInput.fill(PASSWORD);
    await page.getByRole("button", { name: "Sign In" }).click();
    // Wait for the app to load after login
    await page.waitForTimeout(10000);
  } else {
    console.log("Already logged in or on another page");
  }

  // Verify we're actually logged in
  const authCheck = await page.evaluate(async () => {
    const resp = await fetch("/auth/status", { credentials: "include" });
    return resp.json();
  });
  console.log("Post-login auth status:", JSON.stringify(authCheck));

  if (!authCheck.authenticated) {
    throw new Error("Failed to authenticate — session not established");
  }

  // Save auth state (cookies + localStorage)
  await context.storageState({
    path: "./tests/e2e/.auth/state.json",
  });
  console.log("Auth state saved");

  await browser.close();
  await apiContext.dispose();
}

export default globalSetup;
