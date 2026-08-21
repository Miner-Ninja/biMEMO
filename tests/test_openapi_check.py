"""Behaviour contract tests for the OpenAPI spec drift check.

Covers the openapi-contract scenarios: canonical serialization
(S1), the compare behaviour incl. missing and invalid committed files
(S2), the ``--update`` regeneration path (S3), and the drift guard
asserting that the committed ``api/openapi.json`` is exactly the
document the running service advertises (R1, R2).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "openapi_check.py"
COMMITTED_SPEC = Path(__file__).resolve().parents[1] / "api" / "openapi.json"
MODULE_NAME = "openapi_check"


def _load_script_module():
    """Load the drift-check script as an importable module."""
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]

    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


module = _load_script_module()


class TestCanonicalText:
    """S1: the serialization is deterministic and stable."""

    def test_keys_sorted_recursively(self):
        """Key order never depends on document insertion order."""
        text = module.canonical_text({"b": 1, "a": {"y": 2, "x": 3}})
        assert text.index('"a"') < text.index('"b"')
        assert text.index('"x"') < text.index('"y"')

    def test_single_trailing_newline(self):
        """The document ends with exactly one newline."""
        text = module.canonical_text({"a": 1})
        assert text.endswith("}\n")
        assert not text.endswith("\n\n")

    def test_round_trip_is_stable(self):
        """Serializing a parsed canonical text yields the same text."""
        spec = {"a": [1, 2], "b": {"c": True}, "d": None}
        once = module.canonical_text(spec)
        assert module.canonical_text(json.loads(once)) == once


class TestCheck:
    """S2: compare behaviour for missing, invalid, and drifted files."""

    def test_identical_documents_pass(self):
        """No drift and an empty report on success."""
        ok, report = module.check("same text", "same text")
        assert ok
        assert report == ""

    def test_missing_committed_file_fails_with_update_hint(self):
        """A missing spec fails and points at the update mode."""
        ok, report = module.check(None, "generated text")
        assert not ok
        assert "is missing" in report
        assert "--update" in report

    def test_invalid_json_fails_explicitly(self):
        """An invalid committed spec is a contract violation."""
        ok, report = module.check("not json", '{"a": 1}\n')
        assert not ok
        assert "not valid JSON" in report

    def test_drift_report_contains_a_unified_diff(self):
        """The diff shows what the committed file should become."""
        committed = json.dumps({"paths": {"/old": {}}}, indent=2, sort_keys=True) + "\n"
        generated = json.dumps({"paths": {"/new": {}}}, indent=2, sort_keys=True) + "\n"
        ok, report = module.check(committed, generated)
        assert not ok
        assert "differs" in report
        assert '-    "/old": {}' in report
        assert '+    "/new": {}' in report


class TestUpdate:
    """S3: the update mode rewrites the committed spec from the code."""

    def test_update_writes_canonical_generated_document(self, tmp_path):
        """update_spec writes canonical JSON that passes a plain check."""
        original = module.COMMITTED_SPEC
        module.COMMITTED_SPEC = tmp_path / "openapi.json"
        try:
            module.update_spec()
            text = (tmp_path / "openapi.json").read_text(encoding="utf-8")
            assert json.loads(text) == module.generate_spec()
            regenerated = module.canonical_text(module.generate_spec())
            assert module.check(text, regenerated)[0]
        finally:
            module.COMMITTED_SPEC = original


class TestCommittedSpecIsCurrent:
    """Drift guard: the committed spec tracks the code's API surface."""

    def test_committed_spec_matches_generated_document(self):
        """Committed spec equals the document the running service advertises.

        Any route change that does not regenerate the spec fails this
        test (and the CI gate).
        """
        assert COMMITTED_SPEC.is_file(), (
            "api/openapi.json is missing - run "
            "'python scripts/openapi_check.py --update'"
        )
        committed = COMMITTED_SPEC.read_text(encoding="utf-8")
        generated = module.canonical_text(module.generate_spec())
        assert committed == generated
