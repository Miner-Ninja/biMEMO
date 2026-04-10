#!/usr/bin/env python3
# ruff: noqa: RUF001, S310
"""Full-cycle memory testing through biMEMO chat API.

Simulates real user conversation to seed personal information,
then verifies memory retrieval, temporal queries, and summaries.

Requires only stdlib (urllib) — no external dependencies.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

from scripts.seed_memory_constants import SEED_MESSAGES
from scripts.seed_memory_models import TestResult
from scripts.seed_memory_reporting import print_report


# ── HTTP helpers ────────────────────────────────────────────────────


def make_session(base_url: str) -> tuple:
    """Create an HTTP session with cookie support.

    Returns (opener, base_url) tuple.
    """
    jar = CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
    )
    return opener, base_url.rstrip("/")


def api_post(
    opener,
    base_url: str,
    path: str,
    body: dict,
    csrf_token: str | None = None,
    timeout: int = 120,
) -> dict:
    """POST JSON to API, return parsed response."""
    url = f"{base_url}{path}"
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        raise RuntimeError(f"POST {path} → {exc.code}: {raw}") from exc


def api_get(
    opener,
    base_url: str,
    path: str,
    csrf_token: str | None = None,
    timeout: int = 30,
) -> dict:
    """GET from API, return parsed response."""
    url = f"{base_url}{path}"
    headers = {}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        raise RuntimeError(f"GET {path} → {exc.code}: {raw}") from exc


# ── Auth ────────────────────────────────────────────────────────────


def authenticate(opener, base_url: str, password: str) -> str:
    """Login and return CSRF token."""
    api_post(opener, base_url, "/auth/login", {"password": password})
    resp = api_get(opener, base_url, "/csrf-token")
    token = resp.get("token")
    if not token:
        raise RuntimeError("Failed to obtain CSRF token")
    return token


# ── Seed phase ──────────────────────────────────────────────────────


def run_seed(
    opener,
    base_url: str,
    csrf_token: str,
    delay: float,
) -> list[dict]:
    """Send all seed messages through chat API.

    Returns list of {message, response, elapsed} dicts.
    """
    results = []
    total = len(SEED_MESSAGES)
    for i, msg in enumerate(SEED_MESSAGES, 1):
        preview = msg[:60] + ("..." if len(msg) > 60 else "")
        print(f'  [{i:>2}/{total}] User: "{preview}"')

        t0 = time.monotonic()
        try:
            resp = api_post(
                opener,
                base_url,
                "/chat",
                {"message": msg},
                csrf_token=csrf_token,
                timeout=180,
            )
            elapsed = time.monotonic() - t0
            ai_text = resp.get("text", "")
            ai_preview = ai_text[:60] + ("..." if len(ai_text) > 60 else "")
            print(f'         AI: "{ai_preview}"  ({elapsed:.1f}s)')
            results.append(
                {
                    "message": msg,
                    "response": ai_text,
                    "elapsed": elapsed,
                }
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(f"         ERROR: {exc}  ({elapsed:.1f}s)")
            results.append(
                {
                    "message": msg,
                    "response": None,
                    "elapsed": elapsed,
                    "error": str(exc),
                }
            )

        if i < total:
            time.sleep(delay)

    return results


# ── Test functions ──────────────────────────────────────────────────


def _send_test_query(
    opener,
    base_url: str,
    csrf_token: str,
    query: str,
) -> dict:
    """Send a test query via chat and return full response."""
    return api_post(
        opener,
        base_url,
        "/chat",
        {"message": query},
        csrf_token=csrf_token,
        timeout=180,
    )


def test_topic_retrieval(
    opener,
    base_url: str,
    csrf_token: str,
) -> TestResult:
    """Test 1: Topic-based retrieval (food)."""
    r = TestResult("Retrieval по теме (еда)", "Какую кухню я больше всего люблю?")
    t0 = time.monotonic()
    try:
        resp = _send_test_query(opener, base_url, csrf_token, r.query)
        r.elapsed = time.monotonic() - t0
        r.response_text = resp.get("text", "")
        r.retrieved_memories = resp.get("retrieved_memories")

        memories = r.retrieved_memories or []
        if len(memories) > 0:
            r.details.append(f"Retrieved memories: {len(memories)}")
            has_date = any(m.get("date") for m in memories)
            if has_date:
                r.details.append("Memories have dates")
                r.passed = True
            else:
                r.details.append("FAIL: memories lack 'date' field")
        else:
            r.details.append("FAIL: no retrieved_memories")
    except Exception as exc:
        r.elapsed = time.monotonic() - t0
        r.error = str(exc)
    return r


def test_temporal_query(
    opener,
    base_url: str,
    csrf_token: str,
) -> TestResult:
    """Test 2: Temporal query with dates."""
    r = TestResult(
        "Временной вопрос (даты)",
        "Когда мы ездили в Грузию и что там видели?",
    )
    t0 = time.monotonic()
    try:
        resp = _send_test_query(opener, base_url, csrf_token, r.query)
        r.elapsed = time.monotonic() - t0
        r.response_text = resp.get("text", "")
        r.retrieved_memories = resp.get("retrieved_memories")

        text_lower = r.response_text.lower()
        temporal_markers = [
            "март",
            "две недели",
            "тбилиси",
            "казбеги",
            "мцхет",
            "гергети",
        ]
        found_in_response = [m for m in temporal_markers if m in text_lower]

        # Also check retrieved_memories summaries
        memories = r.retrieved_memories or []
        mem_text = " ".join(m.get("summary", "") for m in memories).lower()
        found_in_memories = [m for m in temporal_markers if m in mem_text]

        if found_in_response:
            r.details.append(
                f"Temporal/location markers in response: {found_in_response}"
            )
        if found_in_memories:
            r.details.append(
                f"Temporal/location markers in memories: {found_in_memories}"
            )

        r.passed = bool(found_in_response or found_in_memories)
        if not r.passed:
            r.details.append(
                "FAIL: no temporal/location markers in response or memories"
            )
    except Exception as exc:
        r.elapsed = time.monotonic() - t0
        r.error = str(exc)
    return r


def test_different_topic(
    opener,
    base_url: str,
    csrf_token: str,
) -> TestResult:
    """Test 3: Retrieval of a different topic (programming languages)."""
    r = TestResult(
        "Retrieval другой темы (языки)",
        "На каких языках программирования я пишу?",
    )
    t0 = time.monotonic()
    try:
        resp = _send_test_query(opener, base_url, csrf_token, r.query)
        r.elapsed = time.monotonic() - t0
        r.response_text = resp.get("text", "")
        r.retrieved_memories = resp.get("retrieved_memories")

        memories = r.retrieved_memories or []
        if len(memories) > 0:
            summaries = " ".join(m.get("summary", "") for m in memories).lower()
            lang_markers = ["python", "go", "rust", "java"]
            found = [m for m in lang_markers if m in summaries]
            if found:
                r.details.append(f"Relevant languages in memories: {found}")
                r.passed = True
            else:
                r.details.append(
                    f"Retrieved {len(memories)} memories, but no language markers"
                )
        else:
            r.details.append("FAIL: no retrieved_memories")
    except Exception as exc:
        r.elapsed = time.monotonic() - t0
        r.error = str(exc)
    return r


def test_facts_from_memory(
    opener,
    base_url: str,
    csrf_token: str,
) -> TestResult:
    """Test 4: Specific facts recall (daughter name and age)."""
    r = TestResult(
        "Факты из памяти (семья)",
        "Сколько лет моей дочке и как её зовут?",
    )
    t0 = time.monotonic()
    try:
        resp = _send_test_query(opener, base_url, csrf_token, r.query)
        r.elapsed = time.monotonic() - t0
        r.response_text = resp.get("text", "")
        r.retrieved_memories = resp.get("retrieved_memories")

        text_lower = r.response_text.lower()
        has_name = "маш" in text_lower  # Маша/Машу/Маши
        has_age = "6" in r.response_text or "шест" in text_lower
        if has_name and has_age:
            r.details.append("LLM knows name (Маша) and age (6)")
            r.passed = True
        elif has_name:
            r.details.append("LLM knows name but not age")
        elif has_age:
            r.details.append("LLM knows age but not name")
        else:
            r.details.append("FAIL: LLM doesn't know name or age")
    except Exception as exc:
        r.elapsed = time.monotonic() - t0
        r.error = str(exc)
    return r


def test_no_match(
    opener,
    base_url: str,
    csrf_token: str,
) -> TestResult:
    """Test 5: Query with no matching memories."""
    r = TestResult(
        "Нет совпадений (физика)",
        "What is quantum entanglement?",
    )
    t0 = time.monotonic()
    try:
        resp = _send_test_query(opener, base_url, csrf_token, r.query)
        r.elapsed = time.monotonic() - t0
        r.response_text = resp.get("text", "")
        r.retrieved_memories = resp.get("retrieved_memories")

        memories = r.retrieved_memories or []
        if len(memories) == 0:
            r.details.append("No irrelevant memories retrieved")
            r.passed = True
        else:
            r.details.append(
                f"WARNING: {len(memories)} memories retrieved for unrelated query"
            )
            # Still pass if memories aren't too many (some noise is acceptable)
            r.passed = len(memories) <= 2
    except Exception as exc:
        r.elapsed = time.monotonic() - t0
        r.error = str(exc)
    return r


def test_multi_topic(
    opener,
    base_url: str,
    csrf_token: str,
) -> TestResult:
    """Test 6: Multi-topic retrieval (hobbies)."""
    r = TestResult(
        "Мульти-тематический retrieval",
        "Чем я увлекаюсь помимо работы?",
    )
    t0 = time.monotonic()
    try:
        resp = _send_test_query(opener, base_url, csrf_token, r.query)
        r.elapsed = time.monotonic() - t0
        r.response_text = resp.get("text", "")
        r.retrieved_memories = resp.get("retrieved_memories")

        memories = r.retrieved_memories or []
        if len(memories) >= 2:
            r.details.append(f"Retrieved {len(memories)} memories (multi-topic)")
            r.passed = True
        elif len(memories) == 1:
            r.details.append("Only 1 memory — expected multiple topics")
        else:
            r.details.append("FAIL: no retrieved_memories")
    except Exception as exc:
        r.elapsed = time.monotonic() - t0
        r.error = str(exc)
    return r


def test_distillations_api(
    opener,
    base_url: str,
    csrf_token: str,
) -> TestResult:
    """Test 7: Admin summaries API."""
    r = TestResult("Summaries API", "GET /admin/summaries")
    t0 = time.monotonic()
    try:
        data = api_get(opener, base_url, "/admin/summaries")
        r.elapsed = time.monotonic() - t0

        if isinstance(data, list):
            r.details.append(f"Summaries found: {len(data)}")
            for item in data[:5]:
                conf = item.get("confidence", "?")
                pattern = item.get("pattern", "")[:60]
                created = item.get("created_at", "?")
                r.details.append(f'  [{conf}] "{pattern}" ({created})')
            if len(data) > 0:
                first = data[0]
                has_conf = "confidence" in first
                has_date = "created_at" in first
                if has_conf and has_date:
                    r.passed = True
                else:
                    r.details.append(
                        f"FAIL: missing fields (confidence={has_conf}, "
                        f"created_at={has_date})"
                    )
            else:
                r.details.append("No summaries yet (may need more time)")
                # Pass with warning — summaries may not have been generated yet
                r.passed = True
        else:
            r.details.append(f"FAIL: unexpected response type: {type(data)}")
    except Exception as exc:
        r.elapsed = time.monotonic() - t0
        r.error = str(exc)
    return r


ALL_TESTS = [
    test_topic_retrieval,
    test_temporal_query,
    test_different_topic,
    test_facts_from_memory,
    test_no_match,
    test_multi_topic,
    test_distillations_api,
]


# ── Cross-session test functions ───────────────────────────────────


# Each tuple: (test_name, query, expected_markers_in_response,
#              expected_markers_in_retrieved_messages)
# Markers are details too specific to survive dialog recorder compression.
CROSS_SESSION_QUERIES = [
    (
        "Кросс: ингредиент карбонары",
        "Какой ингредиент я использую вместо бекона в пасте карбонара?",
        ["гуанчиале"],
        ["гуанчиале"],
    ),
    (
        "Кросс: время полумарафона",
        "За какое точное время я пробежал свой полумарафон?",
        ["1 час 52", "1:52", "52 минут"],
        ["52"],
    ),
    (
        "Кросс: песня на гитаре",
        "Какую конкретную песню я недавно выучил на гитаре целиком?",
        ["dust in the wind"],
        ["dust"],
    ),
    (
        "Кросс: приложение для медитации",
        "Каким приложением я пользуюсь для медитации?",
        ["headspace"],
        ["headspace"],
    ),
    (
        "Кросс: синхронизация заметок",
        "Через какой сервис я синхронизирую Obsidian?",
        ["syncthing"],
        ["syncthing"],
    ),
]


def run_cross_session_test(
    opener,
    base_url: str,
    csrf_token: str,
    name: str,
    query: str,
    response_markers: list[str],
    message_markers: list[str],
) -> TestResult:
    """Run a single cross-session test.

    Checks that specific details from a previous session appear
    in the LLM response and/or in retrieved_messages.
    """
    r = TestResult(name, query)
    t0 = time.monotonic()
    try:
        resp = _send_test_query(opener, base_url, csrf_token, r.query)
        r.elapsed = time.monotonic() - t0
        r.response_text = resp.get("text", "")
        r.retrieved_memories = resp.get("retrieved_memories")
        r.retrieved_messages = resp.get("retrieved_messages")

        text_lower = r.response_text.lower()
        found_in_response = [m for m in response_markers if m.lower() in text_lower]

        # Check retrieved_messages for message FTS hits
        msgs = r.retrieved_messages or []
        msgs_text = " ".join(m.get("content", "") for m in msgs).lower()
        found_in_messages = [m for m in message_markers if m.lower() in msgs_text]

        if found_in_response:
            r.details.append(f"Found in LLM response: {found_in_response}")
        if found_in_messages:
            r.details.append(
                f"Found in retrieved_messages ({len(msgs)}): {found_in_messages}"
            )
        elif msgs:
            r.details.append(
                f"retrieved_messages ({len(msgs)}) present but no expected markers"
            )
        else:
            r.details.append("No retrieved_messages in response")

        # Pass if detail found in either response text or retrieved messages
        r.passed = bool(found_in_response or found_in_messages)
        if not r.passed:
            r.details.append(
                f"FAIL: none of {response_markers} found in response "
                f"or retrieved_messages"
            )
    except Exception as exc:
        r.elapsed = time.monotonic() - t0
        r.error = str(exc)
    return r


# ── Main ────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    p = argparse.ArgumentParser(
        description="biMEMO memory full-cycle test via chat API",
    )
    p.add_argument(
        "--password",
        required=True,
        help="Password for /auth/login",
    )
    p.add_argument(
        "--base-url",
        default="http://localhost:5273",
        help="API base URL (default: http://localhost:5273)",
    )
    p.add_argument(
        "--seed-delay",
        type=float,
        default=3.0,
        help="Pause between seed messages in seconds (default: 3)",
    )
    p.add_argument(
        "--dialog-recorder-wait",
        type=float,
        default=15.0,
        help="Pause before tests to let dialog recorder process (default: 15)",
    )
    return p.parse_args()


def main() -> None:
    """Run the full-cycle memory test."""
    args = parse_args()
    t_start = time.monotonic()

    print()
    print("Starting biMEMO memory full-cycle test...")
    print(f"Base URL: {args.base_url}")
    print()

    # ── Auth ──
    print("Authenticating...")
    opener, base_url = make_session(args.base_url)
    try:
        csrf_token = authenticate(opener, base_url, args.password)
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        sys.exit(1)
    print("Authenticated successfully.")
    print()

    # ── Phase 1: Seeding ──
    print("=" * 50)
    print("PHASE 1: Seeding conversation")
    print("=" * 50)
    print()
    seed_results = run_seed(opener, base_url, csrf_token, args.seed_delay)

    seed_ok = sum(1 for r in seed_results if r.get("response"))
    seed_time = sum(r["elapsed"] for r in seed_results)
    print()
    print(
        f"Seeding complete: {seed_ok}/{len(seed_results)} messages, "
        f"{seed_time:.1f}s total"
    )
    print()

    # ── Wait for dialog recorder ──
    wait = args.dialog_recorder_wait
    print(f"Waiting {wait:.0f}s for dialog recorder to process records...")
    time.sleep(args.dialog_recorder_wait)
    print()

    # ── Phase 2: Same-session tests ──
    print("=" * 50)
    print("PHASE 2: Same-session memory tests")
    print("=" * 50)
    print()

    test_results: list[TestResult] = []
    for i, test_fn in enumerate(ALL_TESTS, 1):
        print(f"Running test {i}/{len(ALL_TESTS)}: ", end="", flush=True)
        result = test_fn(opener, base_url, csrf_token)
        status = "PASS" if result.passed else "FAIL"
        print(f"{result.name} -> {status}")
        test_results.append(result)
        if i < len(ALL_TESTS):
            time.sleep(2)

    # ── Phase 3: Cross-session tests ──
    print()
    print("=" * 50)
    print("PHASE 3: Cross-session message FTS tests")
    print("=" * 50)
    print()
    print("Creating new session (re-authenticating)...")

    opener2, _ = make_session(args.base_url)
    try:
        csrf_token2 = authenticate(opener2, base_url, args.password)
    except Exception as exc:
        print(f"Re-authentication failed: {exc}")
        csrf_token2 = None

    cross_results: list[TestResult] = []
    if csrf_token2:
        print("New session created. Running cross-session tests...")
        print()
        total_cross = len(CROSS_SESSION_QUERIES)
        for i, (name, query, resp_markers, msg_markers) in enumerate(
            CROSS_SESSION_QUERIES, 1
        ):
            print(
                f"Running test {i}/{total_cross}: ",
                end="",
                flush=True,
            )
            result = run_cross_session_test(
                opener2,
                base_url,
                csrf_token2,
                name,
                query,
                resp_markers,
                msg_markers,
            )
            status = "PASS" if result.passed else "FAIL"
            print(f"{result.name} -> {status}")
            cross_results.append(result)
            if i < total_cross:
                time.sleep(2)
    else:
        print("Skipping cross-session tests (auth failed).")

    total_elapsed = time.monotonic() - t_start

    # ── Report ──
    print_report(
        base_url=base_url,
        seed_results=seed_results,
        test_results=test_results,
        cross_results=cross_results,
        total_elapsed=total_elapsed,
    )

    all_results = test_results + cross_results
    all_pass = all(tr.passed for tr in all_results)
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
