# biMEMO Code Review — 2026-03-31

## Summary

| Area | Critical | High | Medium | Low | Total |
|------|----------|------|--------|-----|-------|
| Gateway & Agent Loop | 0 | 3 | 5 | 4 | 15 |
| Memory Subsystem | 0 | 0 | 6 | 11 | 17 |
| Security & Trust | 0 | 3 | 4 | 4 | 12 |
| Bridge & Providers | 0 | 1 | 8 | 6 | 19 |
| LLM & Orchestrator | 1 | 3 | 8 | 13 | 25 |
| **Total** | **1** | **10** | **31** | **38** | **88** |

---

## CRITICAL (1)

### C1. orchestrator/runtime.py is 2365 lines (8x the 300-line limit)
- **File**: `app/backend/processes/orchestrator/runtime.py`
- **Impact**: Violates project structure rules. Contains LLM init, bot startup, bridge processing, dialog recorder setup, tool setup, env seeding, and main loop in one file.
- **Fix**: Split into dedicated modules: `llm_setup.py`, `bot_launcher.py`, `bridge_handler.py`, `dialog_recorder_setup.py`, `env_seed.py`.

---

## HIGH (10)

### H1. SSRF TOCTOU — DNS rebinding between validate_url and httpx.get
- **Files**: `app/backend/workers/net_worker.py:206-225`, `app/backend/tools/validation.py:175-191`
- **Impact**: validate_url resolves and checks IP, but httpx.get resolves DNS again independently. Between validation and request, DNS could change (DNS rebinding), allowing fetch to reach internal IP.
- **Fix**: Pin connection to the resolved IP returned by validate_url. Use httpx transport-level config or rewrite URL to use resolved IP with Host header.

### H2. Prompt injection via user profile
- **File**: `app/backend/gateway/agent_loop.py:619-620`
- **Impact**: `_build_profile_section` injects user_profile values directly into system prompt. A malicious display_name like "Ignore all previous instructions..." gets injected verbatim.
- **Fix**: Sanitize display_name and timezone (strip newlines, limit length). Wrap custom_instructions in XML tags or clear delimiters.

### H3. asyncio.gather without return_exceptions in tool execution
- **File**: `app/backend/gateway/agent_loop.py:1761`
- **Impact**: One failing tool aborts all concurrent tool executions, potentially leaving tools in inconsistent state. Error propagates and crashes the entire turn.
- **Fix**: Use `return_exceptions=True` and handle individual failures, or wrap each broker.execute in try/except returning error ToolResult.

### H4. Shared mutable _pending_red overwritten between turns
- **File**: `app/backend/gateway/agent_loop.py:1803, 511`
- **Impact**: `_queue_red_tool_confirmations` replaces `self._pending_red` entirely. If a prior turn's confirmations are unconsumed, they are silently discarded.
- **Fix**: Make _pending_red per-turn or per-conversation. Append instead of replace, or return pending actions from process_message.

### H5. Global DNS timeout mutation
- **File**: `app/backend/tools/validation.py:124`
- **Impact**: `socket.setdefaulttimeout(_DNS_TIMEOUT)` sets timeout globally for the entire process, affecting all socket operations (DB, IPC, HTTP).
- **Fix**: Use asyncio.getaddrinfo or save/restore previous timeout value.

### H6. SSRF validates only first resolved IP
- **File**: `app/backend/tools/validation.py:131-135`
- **Impact**: `_first_dns_ip` checks only the first DNS result. Multi-A-record hostname could have safe first IP and private second IP.
- **Fix**: Validate all resolved IPs from getaddrinfo, not just the first.

### H7. Trust zone bypass — URL regex too permissive
- **File**: `app/backend/trust/zones.py:135-160`
- **Impact**: `extract_user_intent_tools()` matches any `https?://` URL anywhere in user message. URLs in pasted content, code snippets, or quoted text trigger RED zone bypass.
- **Fix**: Require message to be predominantly a URL or start with explicit intent ("fetch", "open"). Don't match URLs embedded in multi-paragraph text.

### H8. Argon2 parameters from encrypted blob — OOM DoS
- **File**: `app/backend/security/encrypted_key_file.py:60-64, 196`
- **Impact**: Attacker who replaces key file could craft blob with extreme memory_kb to cause OOM. Blob is AES-GCM protected, so exploitable only with full file replacement.
- **Fix**: Enforce minimum bounds on deserialized Argon2 parameters before calling derivation.

### H9. Inbound bridge images not validated
- **File**: `app/backend/processes/fastapi_app/routes/chat_bridge.py:128-231`
- **Impact**: Bridge inbound route accepts images without calling validate_image_data_url. Arbitrarily large base64, non-image MIME types, or malformed data URLs pass through to SSE subscribers.
- **Fix**: Add image validation to sync_bridge_inbound_message. Add max_length to images field in ChatBridgeInboundMessageRequest.

### H10. orchestrator/client.py is 1203 lines
- **File**: `app/backend/processes/orchestrator/client.py`
- **Impact**: 4x the 300-line limit. Contains all DB proxy operations plus diagnostic helpers.
- **Fix**: Extract diagnostic parameter builders and domain-specific operation groups into separate modules.

---

## MEDIUM (31)

### Gateway & Agent Loop
- **M1**: Bare `except Exception` in `_append_forced_web_search_context` without narrowing (`agent_loop.py:864, 877`)
- **M2**: `TypeError` in `RECOVERABLE_MEMORY_CALLBACK_ERRORS` silences programming bugs (`agent_loop.py:188`)
- **M3**: `_llm_accepts_sse_queue` returns True on inspection failure — safer default is False (`agent_loop.py:429-434`)
- **M4**: Unbounded messages list growth in model loop — no truncation before LLM call (`agent_loop.py:1629-1663`)
- **M5**: Frozen dataclass `TurnPreparation` with mutable list fields — semantic inconsistency (`agent_loop.py:319`)

### Memory Subsystem
- **M6**: Single oversized message bypasses eviction entirely in zone builder (`zone_builder.py:106-115`)
- **M7**: Non-atomic save_record + mark_tainted — crash between them leaves record without tainted flag (`dialog_recorder/runtime.py:542-589`)
- **M8**: Trust score merges are monotonically increasing — all summaries trend to max score over time (`summary_distiller/store.py:670`)
- **M9**: N+1 query pattern in list_revalidated_distillations — per-row secondary query (`summary_distiller/store.py:281-295, 484-494`)
- **M10**: Event bus subscriber leak — abandoned SSE subscribers accumulate forever (`runtime/event_bus.py`)
- **M11**: Bare `except Exception` catches programming bugs in web_search_classifier (`web_search_classifier.py:72`)

### Security & Trust
- **M12**: Body size bypass via chunked transfer encoding — Content-Length only check (`middleware/body_size.py:67-86`)
- **M13**: Rate limiter is global, not per-client — one user can exhaust limit for all (`channels/endpoints.py:129-190`)
- **M14**: `filter_input` max_size parameter is unused/dead code (`security/content_filter.py:23`)
- **M15**: SessionManager.generation not protected by lock — race in multi-threaded context (`channels/jwt_auth.py:281-296`)

### Bridge & Providers
- **M16**: Discord-specific branching in `_bridge_bearer_token` in deps.py (`deps.py:138-143`)
- **M17**: No max_length on inbound bridge images list — can send hundreds of images (`models/requests.py:182`)
- **M18**: No per-item size limit on inbound bridge image strings (`models/requests.py:182`)
- **M19**: Race condition in outbound bridge sync — concurrent inbound messages can send duplicate outbound (`chat_bridge/service.py:391-395`)
- **M20**: Webhook persona images sent via bot identity, not webhook (`chat_bridge/providers/discord.py:382-389`)
- **M21**: Bridge inbound POST failures silently logged, user gets no feedback (`bot/discord_bot.py:594-619`)
- **M22**: Bridge inbound POST does not handle httpx exceptions (`bot/discord_bot.py:601`)
- **M23**: `validate_inbound_server` returns True when no server configured — permissive default (`chat_bridge/providers/discord.py:96-105`)

### LLM & Orchestrator
- **M24**: No retry logic for LLM calls — transient errors fail immediately (`llm/call_factory.py:87-105`)
- **M25**: tool_choice not in OllamaClient.chat() signature — will raise TypeError (`llm/ollama_client.py`)
- **M26**: OllamaClient.close() does not close lazily-created provider clients — leak (`llm/ollama_client.py:155-157`)
- **M27**: Bare Exception catch in net_worker dispatch leaks internal error details (`workers/net_worker.py:346`)
- **M28**: `_handle_chat` catches bare Exception, returns str(error) to user (`orchestrator/ipc_server.py:212`)
- **M29**: New httpx.AsyncClient per web_search call — no connection pooling (`workers/net_worker.py:102-107`)
- **M30**: New httpx.AsyncClient per http_fetch call — no connection pooling (`workers/net_worker.py:209-213`)
- **M31**: CSRF token dual store — potential divergence between module-level dict and app.state (`deps.py:21`, `routes/auth.py:319`)

---

## LOW (38)

Key items:
- Wrong log levels (warning for debug info) — `agent_loop.py:801`
- Loose `object` type hints where protocols would be clearer — throughout `orchestrator/runtime.py`
- Hardcoded `* 4` in zone_builder instead of referencing CHARS_PER_TOKEN_ESTIMATE — `zone_builder.py:159, 182`
- Dead code/aliases from refactoring — `chat_bridge/provider.py:8-9`
- Token estimation inaccurate for Cyrillic (chars/4 vs chars/2) — `context_builder/constants.py:113`
- Missing docstring params for new parameters
- `callable` used as type hint instead of `Callable` — `registration.py:140`
- `memory_recall` missing from EXPECTED_TOOLS validation set — `registration.py:294-306`
- Function-local import without cycle-breaking comment — `web_search_classifier.py:51`
- No backoff for persistent background task failures — `runtime/scheduler.py:163-164`
- Redundant double `_is_bridge_channel_message` check — `bot/discord_bot.py:621-649`
- Type mismatch: `dict[str, str]` param receives `dict[str, object]` — `bot/discord_bot.py:598`
- `_decode_data_url` can raise uncaught IndexError — `chat_bridge/providers/discord.py:283-288`
- OpenRouter/Ollama timeout inconsistency (120s vs 300s) — `openrouter_client.py:142`
- Hardcoded Ollama token path — `orchestrator/runtime.py:1516`
- Default base URLs as silent fallbacks violate no-defaults rule — `orchestrator/runtime.py:1490-1499`
- `_active_task` in IPC server never set for chat requests — `ipc_server.py:75, 201-219`
- O(n^2) reasoning strip on every stream chunk — `openrouter_provider.py:322-327`

---

## Positive Findings

1. **JWT**: Custom EdDSA with Ed25519, HKDF key derivation, constant-time CSRF via hmac.compare_digest
2. **Encryption**: AES-256-GCM, Argon2id KDF, SQLCipher database encryption
3. **IPC**: HMAC-SHA256 with constant-time comparison, strict message size limits
4. **Content Filtering**: Credential detection, prompt injection, PII, exfiltration, credential redaction
5. **SSRF Prevention**: HTTPS-only, DNS resolution with IP blocklist, per-hop redirect validation
6. **Trust Zones**: Outbound tools always RED, single-use cryptographic confirmation with timeout
7. **Audit**: SHA-256 hash chain for tamper detection, parameterized SQL throughout
8. **Database**: SQLCipher encryption, parameterized queries exclusively
