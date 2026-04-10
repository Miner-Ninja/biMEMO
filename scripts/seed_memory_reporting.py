"""Report rendering helpers for the full-cycle memory test harness."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from scripts.seed_memory_models import TestResult


def _truncate(text: str, limit: int = 80) -> str:
    """Truncate text to the requested width."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _print_test_result(
    test_result: TestResult,
    index: int,
    *,
    width: int = 60,
) -> None:
    """Print one formatted test result block."""
    print(
        f"\u2500\u2500\u2500 Test {index}: {test_result.name} "
        + "\u2500" * max(1, width - 12 - len(f"Test {index}: {test_result.name}"))
    )
    print(f'  Query: "{_truncate(test_result.query)}"')

    if test_result.error:
        print(f"  ERROR: {test_result.error}")
    else:
        memories = test_result.retrieved_memories or []
        if test_result.name != "Summaries API":
            if memories:
                print(f"  Retrieved memories ({len(memories)}):")
                for memory in memories[:5]:
                    date = memory.get("date", "?")
                    summary = _truncate(memory.get("summary", ""), 55)
                    print(f"    \u2022 {date} \u2014 {summary}")
            else:
                print("  Retrieved memories: none")

        messages = test_result.retrieved_messages or []
        if messages:
            print(f"  Retrieved messages ({len(messages)}):")
            for message in messages[:5]:
                date = message.get("date", "?")
                conversation_id = message.get("conversation_id", "?")[:8]
                content = _truncate(message.get("content", ""), 50)
                print(f"    \u2022 {date} [{conversation_id}] {content}")

        if test_result.response_text:
            print(f'  Response: "{_truncate(test_result.response_text)}"')

        for detail in test_result.details:
            print(f"  {detail}")

    status = "\u2713 PASS" if test_result.passed else "\u2717 FAIL"
    print(f"  Status: {status}  ({test_result.elapsed:.1f}s)")
    print()


def print_report(
    *,
    base_url: str,
    seed_results: list[dict],
    test_results: list[TestResult],
    cross_results: list[TestResult],
    total_elapsed: float,
) -> None:
    """Print the final structured report."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    width = 60

    print()
    print("=" * width)
    print("  biMEMO Memory Full-Cycle Test")
    print(f"  Date: {now}")
    print(f"  Base URL: {base_url}")
    print("=" * width)
    print()

    print("\u2501" * 3 + " PHASE 1: Seeding (simulated conversation) " + "\u2501" * 3)
    print()

    seed_ok = sum(1 for result in seed_results if result.get("response"))
    seed_errors = len(seed_results) - seed_ok
    seed_time = sum(result["elapsed"] for result in seed_results)
    print(f"  Messages sent: {len(seed_results)}")
    print(f"  Successful: {seed_ok}, Errors: {seed_errors}")
    print(f"  Total seeding time: {seed_time:.1f}s")
    print()

    print("\u2501" * 3 + " PHASE 2: Same-Session Memory Tests " + "\u2501" * 12)
    print()

    passed = 0
    for index, test_result in enumerate(test_results, 1):
        _print_test_result(test_result, index, width=width)
        if test_result.passed:
            passed += 1

    if cross_results:
        print("\u2501" * 3 + " PHASE 3: Cross-Session Message FTS " + "\u2501" * 12)
        print()

        cross_passed = 0
        for index, test_result in enumerate(cross_results, 1):
            _print_test_result(test_result, index, width=width)
            if test_result.passed:
                cross_passed += 1

        passed += cross_passed

    all_results = test_results + cross_results
    total = len(all_results)
    print("=" * width)
    print(f"  Results: {passed}/{total} PASS")
    if cross_results:
        same_session_passed = sum(
            1 for test_result in test_results if test_result.passed
        )
        cross_session_passed = sum(
            1 for test_result in cross_results if test_result.passed
        )
        print(f"    Same-session: {same_session_passed}/{len(test_results)}")
        print(f"    Cross-session: {cross_session_passed}/{len(cross_results)}")
    minutes = int(total_elapsed // 60)
    seconds = total_elapsed % 60
    if minutes > 0:
        print(f"  Total time: {minutes}m {seconds:.0f}s")
    else:
        print(f"  Total time: {seconds:.1f}s")
    print("=" * width)
