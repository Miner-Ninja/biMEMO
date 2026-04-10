"""Report rendering for the memory stress test."""

from __future__ import annotations

import sys


def print_report(report: dict, chat_results: list[dict]) -> None:
    """Print the final stress-test summary and exit with the final status."""
    print("\n" + "=" * 60)
    print("  MEMORY STRESS TEST REPORT")
    print("=" * 60)

    for check in report["checks"]:
        icon = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠"}[check["status"]]
        line = f"  {icon} [{check['status']}] {check['name']}"
        if check["detail"]:
            line += f" — {check['detail']}"
        print(line)

    print()
    total = report["passed"] + report["failed"] + report["warnings"]
    print(
        f"  Results: {report['passed']}/{total} passed, "
        f"{report['failed']} failed, {report['warnings']} warnings"
    )

    ok_results = [result for result in chat_results if result["status"] == "ok"]
    if ok_results:
        times = [result["elapsed_s"] for result in ok_results]
        average = sum(times) / len(times)
        print(
            f"  Avg response time: {average:.1f}s "
            f"(min={min(times):.1f}s, max={max(times):.1f}s)"
        )

    if report["failed"] > 0:
        print("\n  ✗ SOME CHECKS FAILED — review details above")
        sys.exit(1)

    print("\n  ✓ ALL CHECKS PASSED")
    sys.exit(0)
