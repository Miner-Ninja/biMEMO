# Session Handoff

## Purpose

This file is the restart point for the next coding session.
It documents the current state of the repository, the latest validated commits,
the active runtime configuration, and the next recommended work items.

## Current Focus

The project is in an ongoing `CODE_RULES.md` cleanup pass.

Primary goals of the current phase:

- align naming, imports, docstrings, and test style with `CODE_RULES.md`
- keep Python linting and testing inside Docker only
- preserve runtime behavior unless a real defect is discovered
- commit after every significant cleanup block

## Active Runtime Setup

Application runtime currently uses external Ollama:

- `BIMEMO_LLM_PROVIDER=ollama`
- `BIMEMO_OLLAMA_BASE_URL=https://ai.webapp.my:40009`
- `BIMEMO_OLLAMA_MODEL=qwen3:8b`
- `BIMEMO_OLLAMA_OBSERVER_MODEL=qwen3:8b`

Known runtime status:

- `bimemo` container is expected to serve on `http://127.0.0.1:8010`
- `/health` was previously verified as healthy after restart
- tool-calling with external Ollama was fixed and verified end-to-end

## Key Completed Fixes

These were completed before and during the current cleanup phase:

- fixed refresh flow to use `refresh_token` correctly
- removed LLM API key leakage via query string
- implemented real file replacement semantics
- switched chat attachments to `file_id`
- removed legacy route stack
- fixed Ollama tool-calling compatibility
- fixed SSE/auth refresh issues
- aligned many frontend/backend contracts

## Latest Commits

Recent commits, newest first:

- `444189e` Align process targets and worker tests with conventions
- `1ff7ce5` Align orchestrator main loop with conventions
- `145db48` Align process IPC and app wiring with conventions
- `e391971` Align LLM and search tests with conventions
- `06ee4d8` Add session handoff document
- `a1b77f5` Align foundational tests with conventions
- `9531eb4` Align IPC tests with conventions
- `4fba142` Align memory debug tests with conventions
- `d9d4891` Align bot channels with conventions
- `ea2f60f` Align IPC runtime layer with conventions
- `b0a922c` Align integration route tests with conventions
- `cc8fb48` Align integration test helpers with conventions
- `981ffff` Align user profile tests with naming conventions
- `ab460fc` Make auth audit logging deterministic in tests
- `11597b8` Use shared files directory in chat attachments
- `3b74bd2` Align HTTP and IPC layers with naming conventions
- `66dfcf6` Align tool-calling code with naming conventions
- `1e4a206` Keep Ollama tool call arguments structured
- `ff6c4aa` Preserve tool parameter names in OpenAI schemas
- `5336e11` Enable Ollama tool calls in chat client

## What Was Verified

The following checks were run successfully inside Docker during this cleanup run:

- `ruff check` on each touched backend/test block
- `pytest` on:
  - `tests/test_llm_config_integration.py`
  - `tests/llm_protector/test_db_proxy.py`
  - `tests/llm_protector/test_ipc_channel.py`
  - `tests/test_ipc_auth.py`
  - `tests/test_ipc_models.py`
  - `tests/test_worker_socket.py`
  - `tests/test_discord_bot.py`
  - `tests/test_telegram_bot.py`
  - `tests/test_jwt_auth.py`
  - `tests/test_memory_debug.py`
  - `tests/test_master_key.py`
  - `tests/test_memory_models.py`
  - `tests/test_message_bus.py`
  - `tests/test_messages.py`
  - `tests/test_middleware.py`
  - `tests/test_setup.py`
  - `tests/test_unlock.py`
  - `tests/test_validation.py`
  - `tests/test_vector_store.py`
  - `tests/test_observer.py`
  - `tests/test_reflector.py`
  - `tests/test_ollama_client.py`
  - `tests/test_openrouter_client.py`
  - `tests/test_search.py`
  - `tests/test_net_worker.py`
  - `tests/test_discord_bot.py`
  - `tests/test_telegram_bot.py`
  - `tests/test_worker_manager.py`
  - `tests/test_worker_proxy.py`
  - `tests/llm_protector/test_orchestrator_ipc.py`
  - `tests/llm_protector/test_fastapi_ipc.py`
  - `tests/llm_protector/test_integration.py`

## Important Decisions

- always prefer `app/backend/...` paths in the workspace
- run Python lint/tests in Docker with paths relative to `/app`
- use `apply_patch` for manual file edits
- keep commits small and topic-focused
- if a conventions cleanup reveals a real defect, fix it in the same block and note it

## Notable Defects Found During Cleanup

These were discovered while doing conventions work and already fixed:

- IPC worker shutdown path referenced `sys.exit()` without importing `sys`
- JWT token revocation audit path used fire-and-forget `ensure_future`
- `tests/test_memory_debug.py` fake `messages` table drifted from runtime schema and missed `meta`
- process target and orchestrator modules had multiple type/documentation drifts that would have made the next strict cleanup much riskier

## Next Recommended Work

Next cleanup targets, in recommended order:

1. Remaining production/runtime files with many violations:
   - `app/backend/processes/*` outside the already cleaned files
   - `app/backend/bot/discord_bot.py` may still have more style issues outside the cleaned region
   - remaining `app/backend/*` runtime modules with broad `ruff` debt

2. Remaining larger test clusters:
   - next uncleaned repo-wide test batches from `ruff check tests`

3. Full repo `ruff` reduction in batches instead of trying to fix everything at once.

## Suggested Restart Prompt

Use this in the next session:

`Continue from sandbox/SESSION_HANDOFF.md. Keep working autonomously on CODE_RULES.md cleanup, run Python lint/tests inside Docker only, and create a separate commit after each significant block.`

## Useful Commands

Show recent commits:

```bash
git log --oneline -15
```

Check current worktree:

```bash
git status --short
```

Run lint on a specific Python block inside Docker:

```bash
docker-compose -f docker/docker-compose.dev.yml exec -T bimemo sh -lc "cd /app && ruff check <paths>"
```

Run tests on a specific Python block inside Docker:

```bash
docker-compose -f docker/docker-compose.dev.yml exec -T bimemo python -m pytest <paths> -q
```
