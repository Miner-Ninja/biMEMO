# E2E tests (Playwright)

Browser-level tests for biMEMO against the **full dev stack** (Caddy on
:443 → Vite dev server + backend). The Playwright config does NOT start a
web server (`webServer` is disabled): point the tests at a running stack.

## Prerequisites

- The dev stack is up: `docker compose up -d` in `docker/`
  (services `bimemo`, `frontend`, `caddy`; the backend must report
  `/health` `ok`).
- Chromium is installed (`npx playwright install chromium`).
- The stack's password is already set (first run completed). The shared
  auth state (`tests/e2e/.auth/state.json`) is (re)created automatically
  by `global-setup.ts` on every run — login via the UI, then saved.

## Environment variables

| Variable                 | Default             | Purpose                                                                      |
| ------------------------ | ------------------- | ---------------------------------------------------------------------------- |
| `PLAYWRIGHT_BASE_URL`    | `https://localhost` | Where the Caddy front end listens.                                           |
| `TEST_PASSWORD`          | `testpass123`       | Password global-setup uses to log in.                                        |
| `E2E_BIMEMO_CONTAINER`   | `docker-bimemo-1`   | Backend container for the history-scroll seeder (`docker cp`/`docker exec`). |
| `E2E_SEED_MESSAGE_COUNT` | `250`               | Message count seeded by `history-scroll`.                                    |
| `E2E_SETUP_PASSWORD`     | `setuppass123`      | Password chosen by `setup-first-run` on the disposable stack.                |
| `E2E_SETUP_FIRST_RUN`    | (unset)             | Must be `1` to run `setup-first-run` (manual gate).                          |
| `E2E_SKIP_GLOBAL_SETUP`  | (unset)             | Skip `global-setup` (only for the manual `setup-first-run` run).             |
| `CI`                     | (unset)             | `1` → 2 retries, `forbidOnly`, no dev web server.                            |

## Running

```sh
cd app/frontend

# Smoke: auth-flow, chat-new, history-scroll. NO vLLM needed.
npm run test:e2e:smoke

# Deep: LLM-requiring scenarios (ws-reconnect-fast, setup-first-run...).
# Requires a RUNNING vLLM endpoint configured for the backend.
npm run test:e2e:deep

# Everything (current state: smoke + deep).
npm run test:e2e

# Just list what would run (all files must collect cleanly).
npx playwright test --list
```

### Tags

- `@smoke` — user flows that must pass **without vLLM** (no LLM
  assertions). This is the regular regression pass.
- `@deep` — scenarios that require a running vLLM endpoint (real
  generation). Only run these when the LLM stack is up; otherwise they
  fail at the "streaming starts" step.

## setup-first-run: MANUAL ONLY

`setup-first-run.spec.ts` covers the very first launch: the app with no
password set (`SetupPage`) → create the password → log in.

**It destroys data:** on a stack where the password is already set, or
where the volume holds a real database, running it replaces the password
and can re-create the database. **Never run it against the live stack.**

It is gated: it stays skipped unless `E2E_SETUP_FIRST_RUN=1`. To run it:

1. Build a **disposable** stack — same compose files, but a separate
   project name so its volumes (`keyfile-data`, `db-data`, ...) are
   brand new, e.g. from `docker/`:

   ```sh
   cd docker
   docker compose -p e2e-disposable up -d
   ```

   Compose volumes are namespaced by project name, so
   `e2e-disposable` gets fresh, empty volumes — a genuine first run.
   (Caddy binds :443, so either stop the live stack first, or run the
   disposable stack on another host/VM.)

2. Point the tests at it and run ONLY this file, skipping the global
   setup (it cannot pre-log-in a stack with no password):

   ```sh
   cd app/frontend
   E2E_SETUP_FIRST_RUN=1 E2E_SKIP_GLOBAL_SETUP=1 \
     PLAYWRIGHT_BASE_URL=https://<disposable-host> \
     E2E_SETUP_PASSWORD='my-disposable-password' \
     npx playwright test setup-first-run
   ```

3. Tear it down, including its throwaway volumes:

   ```sh
   cd docker
   docker compose -p e2e-disposable down -v
   ```

## Files

| File                        | Tag      | Notes                                                                                                                                                                                                                                                      |
| --------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `auth-flow.spec.ts`         | `@smoke` | login, wrong password (no session), logout, re-login — isolated contexts, `.auth/state.json` untouched.                                                                                                                                                    |
| `chat-new.spec.ts`          | `@smoke` | create → in list → switch → delete (DELETE 204), no LLM.                                                                                                                                                                                                   |
| `history-scroll.spec.ts`    | `@smoke` | seeds a 250-message scratch conversation via `helpers/seed_history.py` (`docker exec` + the app's `DatabaseManager`); asserts 200 rendered → scroll to top → 250, no duplicates, stable scroll position; deletes the conversation afterwards (DELETE 204). |
| `ws-reconnect-fast.spec.ts` | `@deep`  | WS drop mid-generation → new context with the same session → final answer restored. Needs vLLM; target runtime 60–90 s.                                                                                                                                    |
| `setup-first-run.spec.ts`   | `@deep`  | first launch on a fresh volume. **Manual gate — disposable stack only.**                                                                                                                                                                                   |
| `helpers/seed_history.py`   | —        | runs inside the backend container; seed / `--delete` / `--gone` modes.                                                                                                                                                                                     |
