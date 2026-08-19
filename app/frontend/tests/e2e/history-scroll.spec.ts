import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { expect, test } from "@playwright/test";

const execFileP = promisify(execFile);
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "https://localhost";
// The bimemo backend container (compose service "bimemo").
const CONTAINER = process.env.E2E_BIMEMO_CONTAINER || "docker-bimemo-1";
const SEED_SCRIPT = path.resolve(process.cwd(), "tests/e2e/helpers/seed_history.py");
const TOTAL_MESSAGES = 250;
const FIRST_PAGE = 200; // backend getConversationPage default limit
const OLDER_PAGE = TOTAL_MESSAGES - FIRST_PAGE;
// The regression this spec guards: a FRESH page load lands directly on the
// conversation. The app's first render is the empty state, which has no
// scroll viewport in the DOM; the pagination listener must attach the moment
// the message list appears, or scrolling up at the top of the loaded window
// would silently load no older page (the bug this used to expose needed a
// sidebar dance around to work at all).
const VIEWPORT_SELECTOR = '.message-scroll-area [data-slot="scroll-area-viewport"]';
const ROW_SELECTOR =
  '.message-scroll-area [data-slot="scroll-area-viewport"] .divide-y > div';

interface StoredMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
}

interface MessagePage {
  messages: StoredMessage[];
  next_cursor: string | null;
  has_more: boolean;
}

async function runSeeder(
  args: string[],
  seedEnv: Record<string, string> = {}
): Promise<string> {
  await execFileP("docker", ["cp", SEED_SCRIPT, `${CONTAINER}:/tmp/seed_history.py`]);
  const envFlags = Object.entries(seedEnv).flatMap(([k, v]) => ["-e", `${k}=${v}`]);
  const { stdout } = await execFileP("docker", [
    "exec",
    ...envFlags,
    CONTAINER,
    "python3",
    "/tmp/seed_history.py",
    ...args,
  ]);
  return stdout;
}

/** Last non-empty line of the seeder output = the conversation id. */
function idFrom(output: string): string {
  return output.trim().split("\n").filter(Boolean).pop() ?? "";
}

/** First message row that is (at least partially) visible at the top of the viewport. */
function measureTopMessage(viewportEl: Element): {
  text: string;
  offset: number;
  scrollTop: number;
  domIndex: number;
} {
  const container = viewportEl.querySelector(".divide-y");
  const viewportTop = viewportEl.getBoundingClientRect().top;
  if (container) {
    const children = Array.from(container.children) as HTMLElement[];
    for (let i = 0; i < children.length; i++) {
      const el = children[i];
      const rect = el.getBoundingClientRect();
      if (rect.bottom > viewportTop + 1) {
        return {
          text: (el.textContent || "").trim().slice(0, 32),
          offset: rect.top - viewportTop,
          scrollTop: viewportEl.scrollTop,
          domIndex: i,
        };
      }
    }
  }
  return { text: "", offset: 0, scrollTop: viewportEl.scrollTop, domIndex: -1 };
}

function findMessageOffset(viewportEl: Element, needle: string): number | null {
  const container = viewportEl.querySelector(".divide-y");
  const viewportTop = viewportEl.getBoundingClientRect().top;
  if (!container) return null;
  for (const el of Array.from(container.children) as HTMLElement[]) {
    if ((el.textContent || "").includes(needle)) {
      return el.getBoundingClientRect().top - viewportTop;
    }
  }
  return null;
}
/** DOM index (among the rendered rows) of the row containing the needle. */
function findMessageIndex(viewportEl: Element, needle: string): number | null {
  const container = viewportEl.querySelector(".divide-y");
  if (!container) return null;
  const children = Array.from(container.children) as HTMLElement[];
  for (let i = 0; i < children.length; i++) {
    if ((children[i].textContent || "").includes(needle)) {
      return i;
    }
  }
  return null;
}

/**
 * Extract the seed number from a row's text ("You 23:45 seed 50" -> 50).
 * The row text starts with the role and a timestamp, so parsing the LEADING
 * digits would capture the time, not the seed.
 */
function seedNumber(text: string): number {
  const match = /seed (\d+)/.exec(text);
  return match ? parseInt(match[1], 10) : Number.NaN;
}

test.describe("history scroll pagination", () => {
  // 180s covers the tests and their in-body seeder steps (docker exec +
  // encrypted-DB writes can exceed the 30s default under host load or DB
  // lock contention with the backend). Seeding lives in the test body
  // because Playwright does not extend this timeout to beforeAll/afterAll
  // hooks — a 30s hook budget is too small for a docker-exec seeder.
  test.describe.configure({ timeout: 180_000 });
  let mainId = "";
  let cleanupDone = false;

  // Best-effort cleanup of this attempt's seed, run in parallel so the
  // short 30s hook budget is respected even under host load. Skipped when
  // the in-test cleanup already removed the conversation (that one is
  // authoritative); no assertions here — the in-test DELETE 204 check is
  // the real cleanup requirement.
  test.afterAll(async () => {
    if (cleanupDone || !mainId) {
      return;
    }
    // Bound the whole hook well inside the 30s hook budget: if a docker
    // exec is that slow, the next attempt's step 0 will pick the leftover
    // up anyway.
    const work = runSeeder(["--delete", mainId], {});
    const deadline = new Promise<void>((resolve) =>
      setTimeout(resolve, 20_000).unref()
    );
    await Promise.race([work, deadline]);
  });

  test("scrolls from 200 to 250 unique messages without a position jump @smoke", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    // 0. A fresh scratch conversation: remove the previous attempt's
    //    leftover first (a failed attempt can leave its seed behind),
    //    then seed.
    if (mainId) {
      try {
        await runSeeder(["--delete", mainId], {});
      } catch {
        // best effort — the fresh seed below is what matters
      }
      mainId = "";
    }
    // 250-message conversation: the pagination subject (seeder defaults).
    mainId = idFrom(await runSeeder([], {}));
    expect(mainId).toMatch(/^[0-9a-f]{8}-[0-9a-f-]{27}$/);
    test.info().annotations.push({ type: "seed-main", description: mainId });

    // Capture every message page of the seeded conversation. The older
    // (cursor) page response is HELD until the "before" scroll state is
    // measured, so the prepend is deterministic.
    let releaseOlderPage: () => void = () => undefined;
    const olderPageReleased = new Promise<void>((resolve) => {
      releaseOlderPage = resolve;
    });
    let firstPage: MessagePage | null = null;
    let olderPage: MessagePage | null = null;

    await page.route("**/chat/conversations/**", async (route) => {
      const url = route.request().url();
      if (!url.includes(`/${mainId}/messages?`)) {
        await route.continue();
        return;
      }
      const upstream = await route.fetch();
      const body = await upstream.text();
      const headers = Object.fromEntries(
        upstream.headersArray().map((h) => [h.name, h.value])
      );
      if (url.includes("cursor=")) {
        olderPage = JSON.parse(body) as MessagePage;
        await olderPageReleased;
      } else {
        firstPage = JSON.parse(body) as MessagePage;
      }
      await route.fulfill({ status: upstream.status(), headers, body });
    });

    const rows = page.locator(ROW_SELECTOR);
    const viewport = page.locator(VIEWPORT_SELECTOR);

    try {
      // 1. The regression scenario: a FRESH page load lands directly on the
      //    seeded conversation (no sidebar navigation first). At this moment
      //    the message list is not in the DOM yet (empty state renders
      //    first), so the pagination listener attaches only after the list
      //    appears — this is the exact state the bug used to leave dead.
      await page.goto(`${BASE_URL}/c/${mainId}`);
      await expect(rows).toHaveCount(FIRST_PAGE, { timeout: 30_000 });

      // 2. First page: the 200 newest messages are rendered.
      expect(firstPage, "first messages page was not captured").not.toBeNull();
      expect(firstPage!.messages).toHaveLength(FIRST_PAGE);
      expect(firstPage!.has_more).toBe(true);
      expect(firstPage!.next_cursor).not.toBeNull();
      // Newest page covers seeds 50..249, newest last.
      const firstContents = firstPage!.messages.map((m) => m.content);
      expect(firstContents).toContain(`seed ${TOTAL_MESSAGES - 1}`);
      expect(firstContents).not.toContain("seed 0");

      // 3. Scroll to the top of the loaded window: wheel up (which also
      //    releases the auto stick-to-bottom behaviour), with a
      //    deterministic fallback if wheel scrolling was throttled.
      await page.waitForTimeout(700); // let the initial auto-scroll settle
      let reachedTop = false;
      for (let i = 0; i < 120 && !reachedTop; i++) {
        await viewport.hover();
        await page.mouse.wheel(0, -1500);
        reachedTop = await viewport.evaluate((el) => el.scrollTop < 80);
        if (!reachedTop) {
          await page.waitForTimeout(100);
        }
      }
      if (!reachedTop) {
        await viewport.evaluate((el) => {
          el.scrollTop = 0;
        });
      }
      await expect
        .poll(() => viewport.evaluate((el) => el.scrollTop), { timeout: 10_000 })
        .toBeLessThan(80);

      // 4. Top of the loaded window: measure the anchor message BEFORE the
      //    older page arrives (the route is still holding it). The top
      //    visible row is the oldest row of the first page (scrollTop is
      //    below 80px, so one of the first two rows).
      const before = await viewport.evaluate(measureTopMessage);
      expect(before.text, "no top message visible before preload").not.toBe("");
      const beforeSeed = seedNumber(before.text);
      expect(Number.isNaN(beforeSeed), "anchor row is not a seed row").toBe(false);
      expect(beforeSeed).toBeGreaterThanOrEqual(OLDER_PAGE);
      expect(beforeSeed).toBeLessThan(OLDER_PAGE + 4);
      releaseOlderPage();

      // 5. Older page (50 oldest seeds) is prepended -> 250 rendered.
      await expect(rows).toHaveCount(TOTAL_MESSAGES, { timeout: 30_000 });
      expect(olderPage, "older messages page was not captured").not.toBeNull();
      expect(olderPage!.messages).toHaveLength(OLDER_PAGE);
      expect(olderPage!.has_more).toBe(false);
      expect(olderPage!.next_cursor).toBeNull();

      // 6. No duplicates: the two pages cover exactly seeds 0..249 and the
      //    rendered row count equals the number of unique messages.
      const allContents = [
        ...olderPage!.messages.map((m) => m.content),
        ...firstPage!.messages.map((m) => m.content),
      ];
      expect(new Set(allContents).size).toBe(TOTAL_MESSAGES);
      for (let i = 0; i < TOTAL_MESSAGES; i++) {
        expect(allContents).toContain(`seed ${i}`);
      }
      expect(await rows.count()).toBe(TOTAL_MESSAGES);

      // 7. The anchor message survives the prepend at exactly the right
      //    place in the DOM: exactly OLDER_PAGE rows were inserted above
      //    it (no duplicates, no reordering).
      await page.waitForTimeout(500); // let the compensation layout effect settle
      const anchorIndex = await viewport.evaluate(findMessageIndex, before.text);
      expect(anchorIndex, "anchor message not found after prepend").not.toBeNull();
      expect(anchorIndex).toBe(before.domIndex + OLDER_PAGE);
      const afterOffset = await viewport.evaluate(findMessageOffset, before.text);
      if (afterOffset !== null) {
        console.log(
          `  [position] anchor offset: before ${before.offset}px, after ${afterOffset}px (delta ${Math.abs(afterOffset - before.offset)}px)`
        );
      }

      // 8. No position jump: the viewport stays anchored on the same
      //    message. The top visible row is the anchor (or within a couple
      //    of rows of it — sub-row drift), not the bottom of the history
      //    or the very top of all history.
      const after = await viewport.evaluate(measureTopMessage);
      const afterSeed = seedNumber(after.text);
      console.log(
        `  [position] viewport top after prepend: "${after.text}" (scrollTop ${after.scrollTop})`
      );
      expect(Number.isNaN(afterSeed), "no seed row visible after prepend").toBe(false);
      expect(Math.abs(afterSeed - beforeSeed)).toBeLessThanOrEqual(2);
    } finally {
      // Cleanup: the scratch conversation is deleted (DELETE -> 204).
      releaseOlderPage(); // idempotent — unblocks the held route, if any
      let deleted = false;
      if (mainId) {
        const csrf = await (await page.request.get("/csrf-token")).json();
        const del = await page.request.delete(`/chat/conversations/${mainId}`, {
          headers: { "X-CSRF-Token": csrf.token as string },
        });
        if (del.status() === 204) {
          deleted = true;
        } else {
          // Fallback: delete the rows directly.
          try {
            await runSeeder(["--delete", mainId], {});
            deleted = true;
          } catch {
            // keep the finally block from failing the test on cleanup errors
          }
        }
      }
      // Mark done only on a confirmed deletion; a failed one is picked up
      // by the next attempt's step 0.
      cleanupDone = deleted;
    }
  });
});
