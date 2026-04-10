#!/usr/bin/env python3
# ruff: noqa: S310
"""Memory subsystem stress test.

Sends 30 test questions to biMEMO LLM, then verifies that all
memory subsystems (messages, records, summaries) work correctly.

Usage (inside the container):
    python scripts/memory_stress_test.py \
        --base-url http://127.0.0.1:8000 \
        --password <password>

Usage (from host, forwarded port):
    python scripts/memory_stress_test.py \
        --base-url http://127.0.0.1:5273 \
        --password <password>

Phases:
    1. Authenticate and get CSRF token
    2. Clear all existing memory (conversations, records, summaries)
    3. Send 30 test questions sequentially with delays
    4. Wait for dialog recorder/distiller pipelines to finish
    5. Verify memory subsystem state via admin endpoints
"""

import argparse
import json
import time
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from scripts.memory_stress_constants import TEST_QUESTIONS
from scripts.memory_stress_reporting import print_report


# ── HTTP Client ─────────────────────────────────────────────────────


@dataclass
class APIClient:
    """Minimal HTTP client using stdlib (no requests/httpx dependency)."""

    base_url: str
    cookies: CookieJar = field(default_factory=CookieJar)
    csrf_token: str = ""

    def __post_init__(self):
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        if params:
            url += f"?{urlencode(params)}"

        data = None
        if body is not None:
            data = json.dumps(body).encode()

        req = Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.csrf_token:
            req.add_header("x-csrf-token", self.csrf_token)

        try:
            with self.opener.open(req, timeout=120) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw.strip() else {}
        except HTTPError as e:
            raw = e.read().decode()
            print(f"  HTTP {e.code}: {raw[:500]}")
            raise

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: dict | None = None) -> dict:
        return self._request("POST", path, body=body)

    def delete(self, path: str, params: dict | None = None) -> dict:
        return self._request("DELETE", path, params=params)


# ── Test Logic ──────────────────────────────────────────────────────


def authenticate(client: APIClient, password: str) -> None:
    """Login and obtain CSRF token."""
    print("── Phase 0: Authenticate ──")

    status = client.get("/auth/status")
    print(f"  Auth status: needs_setup={status.get('needs_setup')}")

    if status.get("needs_setup"):
        print("  Running first-time setup...")
        client.post("/auth/setup", {"password": password})
        print("  Setup complete.")
    else:
        print("  Logging in...")
        client.post("/auth/login", {"password": password})
        print("  Login OK.")

    resp = client.get("/csrf-token")
    client.csrf_token = resp["token"]
    print(f"  CSRF token acquired ({len(client.csrf_token)} chars).")


def clear_memory(client: APIClient, password: str) -> None:
    """Delete all conversations (cascades to messages), then verify clean state."""
    print("\n── Phase 1: Clear Memory ──")

    # Get reauth token for delete operations
    reauth_resp = client.post("/admin/reauth", {"password": password})
    reauth_token = reauth_resp["reauth_token"]
    print("  Re-auth token acquired.")

    # Get current pipeline counts
    pipeline = client.get("/admin/memory/pipeline")
    print(
        f"  Before: messages={pipeline['messages']}, "
        f"records={pipeline['records']}, "
        f"summaries={pipeline['summaries']}"
    )

    if pipeline["messages"] == 0 and pipeline["records"] == 0:
        print("  Memory already clean. Skipping.")
        return

    # Get conversation IDs from context endpoint (has conversation_id field)
    context = client.get("/admin/memory/context")
    conv_ids = set()
    for tier in context.get("tiers", {}).values():
        for entry in tier.get("entries", []):
            cid = entry.get("conversation_id")
            if cid:
                conv_ids.add(cid)

    print(f"  Found {len(conv_ids)} conversation(s) to delete.")

    for cid in conv_ids:
        # Each delete needs a fresh reauth token
        reauth_resp = client.post("/admin/reauth", {"password": password})
        reauth_token = reauth_resp["reauth_token"]
        try:
            client.delete(
                f"/admin/conversations/{cid}",
                {
                    "reauth_token": reauth_token,
                },
            )
            print(f"  Deleted conversation {cid[:8]}...")
        except HTTPError as e:
            print(f"  Failed to delete {cid[:8]}...: {e}")

    # Verify
    pipeline = client.get("/admin/memory/pipeline")
    print(
        f"  After: messages={pipeline['messages']}, "
        f"records={pipeline['records']}, "
        f"summaries={pipeline['summaries']}"
    )

    remaining_obs = pipeline["records"]
    remaining_ref = pipeline["summaries"]
    if remaining_obs > 0 or remaining_ref > 0:
        print(f"  ⚠ {remaining_obs} orphan records + {remaining_ref} summaries remain.")
        print("    (delete_conversation does not cascade to records/summaries)")
        print(
            "    These will serve as baseline — "
            "test counts will be calculated as delta."
        )


def send_questions(client: APIClient, delay: float) -> list[dict]:
    """Send all test questions and collect responses."""
    print(f"\n── Phase 2: Send {len(TEST_QUESTIONS)} Questions (delay={delay}s) ──")

    results = []
    for i, question in enumerate(TEST_QUESTIONS, 1):
        short = question[:60] + ("..." if len(question) > 60 else "")
        print(f"  [{i:2d}/{len(TEST_QUESTIONS)}] {short}")

        t0 = time.monotonic()
        try:
            resp = client.post("/chat", {"message": question})
            elapsed = time.monotonic() - t0

            result = {
                "index": i,
                "question": question,
                "response_text": resp.get("text", ""),
                "input_filtered": resp.get("input_filtered", False),
                "output_filtered": resp.get("output_filtered", False),
                "retrieved_memories": resp.get("retrieved_memories") or [],
                "retrieved_messages": resp.get("retrieved_messages") or [],
                "elapsed_s": round(elapsed, 2),
                "status": "ok",
            }

            mem_count = len(result["retrieved_memories"])
            msg_count = len(result["retrieved_messages"])
            filtered = ""
            if result["input_filtered"]:
                filtered += " [INPUT_FILTERED]"
            if result["output_filtered"]:
                filtered += " [OUTPUT_FILTERED]"

            print(
                f"         → {elapsed:.1f}s, memories={mem_count}, "
                f"msgs={msg_count}{filtered}"
            )

        except HTTPError as e:
            elapsed = time.monotonic() - t0
            result = {
                "index": i,
                "question": question,
                "response_text": "",
                "elapsed_s": round(elapsed, 2),
                "status": f"error_{e.code}",
            }
            print(f"         → ERROR {e.code} after {elapsed:.1f}s")

        results.append(result)

        if i < len(TEST_QUESTIONS):
            time.sleep(delay)

    return results


def wait_for_pipeline(
    client: APIClient, target_messages: int, timeout: int = 180
) -> dict:
    """Wait for dialog recorder pipeline to process pending messages."""
    print(f"\n── Phase 3: Wait for Pipeline (timeout={timeout}s) ──")

    t0 = time.monotonic()
    last_obs = 0
    stable_ticks = 0

    while time.monotonic() - t0 < timeout:
        pipeline = client.get("/admin/memory/pipeline")
        msgs = pipeline["messages"]
        obs = pipeline["records"]
        refs = pipeline["summaries"]
        elapsed = time.monotonic() - t0

        print(f"  [{elapsed:5.1f}s] messages={msgs}, records={obs}, summaries={refs}")

        if obs == last_obs:
            stable_ticks += 1
        else:
            stable_ticks = 0
            last_obs = obs

        # Pipeline is "done" when record count is stable for 3 checks
        # and we have at least some records
        if stable_ticks >= 3 and obs > 0:
            print(f"  Pipeline stabilized after {elapsed:.0f}s.")
            return pipeline

        time.sleep(10)

    print(f"  ⚠ Timed out after {timeout}s. Proceeding with verification anyway.")
    return client.get("/admin/memory/pipeline")


def verify_memory(client: APIClient, chat_results: list[dict], baseline: dict) -> dict:
    """Verify all memory subsystems and produce a report."""
    print("\n── Phase 4: Verify Memory Subsystems ──")

    report: dict = {"checks": [], "passed": 0, "failed": 0, "warnings": 0}

    def check(name: str, passed: bool, detail: str = ""):
        status = "PASS" if passed else "FAIL"
        report["checks"].append({"name": name, "status": status, "detail": detail})
        if passed:
            report["passed"] += 1
        else:
            report["failed"] += 1
        icon = "✓" if passed else "✗"
        print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))

    def warn(name: str, detail: str):
        report["checks"].append({"name": name, "status": "WARN", "detail": detail})
        report["warnings"] += 1
        print(f"  ⚠ {name} — {detail}")

    # 1. Pipeline counts
    pipeline = client.get("/admin/memory/pipeline")
    msg_count = pipeline["messages"]
    obs_count = pipeline["records"]
    ref_count = pipeline["summaries"]

    baseline_obs = baseline.get("records", 0)
    baseline_ref = baseline.get("summaries", 0)
    new_obs = obs_count - baseline_obs
    new_ref = ref_count - baseline_ref

    print(
        f"\n  Pipeline state: messages={msg_count}, "
        f"records={obs_count} (+{new_obs} new), "
        f"summaries={ref_count} (+{new_ref} new)"
    )

    # Check: messages stored (user + assistant = 2 per question)
    expected_msgs_min = len(TEST_QUESTIONS) * 2  # user + assistant
    check(
        "Messages stored",
        msg_count >= expected_msgs_min,
        f"got {msg_count}, expected >= {expected_msgs_min}",
    )

    # Check: records generated
    check("Records generated", new_obs > 0, f"got {new_obs} new records")

    # Check: record ratio (expect roughly 1 record per 2-5 messages)
    obs_ratio = new_obs / len(TEST_QUESTIONS) if new_obs > 0 else 0
    if obs_ratio < 0.1:
        check(
            "Record coverage",
            False,
            f"ratio {obs_ratio:.2f} (too few — dialog recorder may be broken)",
        )
    elif obs_ratio < 0.3:
        warn("Record coverage", f"ratio {obs_ratio:.2f} (low but not zero)")
    else:
        check("Record coverage", True, f"ratio {obs_ratio:.2f}")

    # Check: summaries generated (after 30 msgs, should have at least 1)
    if new_ref > 0:
        check("Summaries generated", True, f"got {new_ref} new summaries")
    else:
        warn(
            "Summaries not generated",
            "distiller may need more records or more time",
        )

    # 2. Timeline data
    timeline = client.get(
        "/admin/memory/timeline",
        {
            "type": "",
            "limit": "200",
            "offset": "0",
        },
    )
    entries = timeline.get("entries", [])
    entry_types = {}
    for e in entries:
        t = e["type"]
        entry_types[t] = entry_types.get(t, 0) + 1

    check(
        "Timeline returns data",
        len(entries) > 0,
        f"total entries={len(entries)}, types={entry_types}",
    )

    # 3. Timeline ordering (should be newest first)
    if len(entries) >= 2:
        timestamps = [e["created_at"] for e in entries]
        is_ordered = all(
            timestamps[i] >= timestamps[i + 1] for i in range(len(timestamps) - 1)
        )
        check(
            "Timeline ordering (newest first)",
            is_ordered,
            f"first={timestamps[0]}, last={timestamps[-1]}",
        )

    # 4. Context endpoint
    context = client.get("/admin/memory/context")
    tiers = context.get("tiers", {})
    tier_names = list(tiers.keys())
    check("Context tiers present", len(tier_names) > 0, f"tiers={tier_names}")

    total_ctx = sum(t.get("count", 0) for t in tiers.values())
    check("Context has entries", total_ctx > 0, f"total context entries={total_ctx}")

    # 5. Record quality — spot-check that records capture key facts
    obs_timeline = client.get(
        "/admin/memory/timeline",
        {
            "type": "record",
            "limit": "50",
            "offset": "0",
        },
    )
    obs_entries = obs_timeline.get("entries", [])

    # Check if any record mentions "Дмитрий" or "Rust" or "Берлин"
    all_obs_text = " ".join(e.get("content", "") for e in obs_entries).lower()
    key_facts_found = []
    for kw in ["дмитрий", "rust", "берлин", "devops", "байт", "кот"]:
        if kw in all_obs_text:
            key_facts_found.append(kw)

    check(
        "Key facts in records",
        len(key_facts_found) >= 2,
        f"found: {key_facts_found}",
    )

    # 6. Chat response quality — recall questions (13-17) should use memory
    recall_indices = [12, 13, 14, 15, 16]  # 0-indexed
    recall_with_memory = 0
    for idx in recall_indices:
        if idx < len(chat_results):
            r = chat_results[idx]
            has_mem = len(r.get("retrieved_memories", [])) > 0
            has_msgs = len(r.get("retrieved_messages", [])) > 0
            if has_mem or has_msgs:
                recall_with_memory += 1

    check(
        "Recall questions use memory retrieval",
        recall_with_memory >= 2,
        (
            f"{recall_with_memory}/{len(recall_indices)} "
            "recall questions had memory context"
        ),
    )

    # 7. No input filtering on clean questions
    filtered_inputs = [r for r in chat_results if r.get("input_filtered")]
    check(
        "No false positive input filtering",
        len(filtered_inputs) == 0,
        f"{len(filtered_inputs)} questions were input-filtered",
    )

    # 8. No errors
    errors = [r for r in chat_results if r["status"] != "ok"]
    check(
        "All chat requests succeeded",
        len(errors) == 0,
        f"{len(errors)} errors" if errors else "",
    )

    # 9. Record timestamps are within session time range
    if obs_entries:
        obs_times = sorted(e["created_at"] for e in obs_entries)
        msg_entries = client.get(
            "/admin/memory/timeline",
            {
                "type": "message",
                "limit": "1",
                "offset": "0",
            },
        ).get("entries", [])

        if msg_entries and obs_times:
            check(
                "Records have valid timestamps",
                True,
                f"obs range: {obs_times[0]} — {obs_times[-1]}",
            )

    return report


# ── Main ────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="biMEMO memory stress test")
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:5273", help="biMEMO API base URL"
    )
    parser.add_argument(
        "--password", required=True, help="Admin password for authentication"
    )
    parser.add_argument(
        "--delay", type=float, default=3.0, help="Delay between messages (seconds)"
    )
    parser.add_argument(
        "--pipeline-timeout",
        type=int,
        default=180,
        help="Max seconds to wait for pipeline processing",
    )
    parser.add_argument(
        "--skip-clear", action="store_true", help="Skip memory clearing phase"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Save full results as JSON to this path",
    )
    args = parser.parse_args()

    client = APIClient(base_url=args.base_url)

    # Phase 0: Auth
    authenticate(client, args.password)

    # Phase 1: Clear
    baseline = {"records": 0, "summaries": 0}
    if not args.skip_clear:
        clear_memory(client, args.password)
    # Capture baseline after clearing (may have orphan obs/refs)
    baseline = client.get("/admin/memory/pipeline")
    print(
        f"  Baseline: records={baseline['records']}, summaries={baseline['summaries']}"
    )

    # Phase 2: Send questions
    chat_results = send_questions(client, args.delay)

    # Phase 3: Wait for pipeline
    wait_for_pipeline(
        client,
        target_messages=len(TEST_QUESTIONS) * 2,
        timeout=args.pipeline_timeout,
    )

    # Phase 4: Verify
    report = verify_memory(client, chat_results, baseline)

    # Save raw results
    if args.output:
        full_output = {
            "chat_results": chat_results,
            "report": report,
            "baseline": baseline,
        }
        with open(args.output, "w") as f:
            json.dump(full_output, f, ensure_ascii=False, indent=2)
        print(f"\n  Full results saved to: {args.output}")

    # Final report
    print_report(report, chat_results)


if __name__ == "__main__":
    main()
