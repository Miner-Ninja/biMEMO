"""Tests for the pre-deployment env-file validation gate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_env.py"
ENV_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / ".env.example"
MODULE_NAME = "validate_env"


def _load_script_module():
    """Load the validator script as an importable module."""
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

# Non-empty values for the sixteen required variables (R2).
COMPLETE_ENV: dict[str, str] = {
    "BIMEMO_DEBUG": "false",
    "BIMEMO_HOST": "127.0.0.1",
    "BIMEMO_PORT": "8000",
    "BIMEMO_KEY_SOURCE": "password",
    "BIMEMO_ENCRYPTED_KEY_FILE_PATH": "/data/keyfile/master.key.enc",
    "BIMEMO_AUTOMATIC_UNLOCK": "false",
    "BIMEMO_DATABASE_PATH": "/data/db/bimemo.db",
    "BIMEMO_UPLOADED_FILES_DIR": "/data/files",
    "BIMEMO_DIAGNOSTICS_RETENTION_DAYS": "14",
    "BIMEMO_SEARXNG_URL": "http://searxng:8080/search",
    "BIMEMO_LLM_RESPONSE_FORMAT": "json_schema",
    "BIMEMO_LLM_RESPONSE_STREAM": "true",
    "BIMEMO_LLM_NATIVE_TOOLS_SUPPORT": "unknown",
    "BIMEMO_LLM_EXACT_NAMED_CHOICE_SUPPORT": "unknown",
    "BIMEMO_LLM_REQUIRED_NATIVE_DECISION_SUPPORT": "unknown",
    "BIMEMO_LLM_CUSTOM_SCHEMA_SUPPORT": "unknown",
}


def _write_env(tmp_path: Path, values: dict[str, str]) -> Path:
    """Write an env file with the given variable mapping."""
    env_path = tmp_path / "test.env"
    env_path.write_text(
        "\n".join(f"{name}={value}" for name, value in values.items()) + "\n",
        encoding="utf-8",
    )
    return env_path


def _run(path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run the gate against an env path and return (exit code, stdout, stderr)."""
    exit_code = module.main(["validate_env.py", str(path)])
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def _required_from_env_example() -> set[str]:
    """Collect the variables under the (REQUIRED) sections of .env.example."""
    required: set[str] = set()
    in_required_section = False
    for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("# ---"):
            in_required_section = "REQUIRED" in stripped
        if (
            not in_required_section
            or not stripped
            or stripped.startswith("#")
            or "=" not in stripped
        ):
            continue
        name = stripped.split("=", 1)[0].strip()
        if name:
            required.add(name)
    return required


class TestValidateEnv:
    """Verify the env-file gate against the behaviour contract."""

    def test_all_required_present_exits_zero(self, tmp_path, capsys):
        """S1.1: a complete env file passes with exactly one OK: line."""
        env_path = _write_env(tmp_path, dict(COMPLETE_ENV))

        code, out, _ = _run(env_path, capsys)

        assert code == 0
        assert len([line for line in out.splitlines() if line.startswith("OK:")]) == 1

    def test_bom_prefixed_env_still_passes(self, tmp_path, capsys):
        """S1.1: a complete env file saved with a leading UTF-8 BOM still passes.

        The first variable name parses without the BOM character.
        """
        env_path = tmp_path / "bom.env"
        env_path.write_text(
            "\n".join(f"{name}={value}" for name, value in COMPLETE_ENV.items()) + "\n",
            encoding="utf-8-sig",
        )

        code, out, _ = _run(env_path, capsys)

        assert code == 0
        assert len([line for line in out.splitlines() if line.startswith("OK:")]) == 1

    def test_missing_file_fails_explicitly(self, tmp_path, capsys):
        """S1.2: a non-existent path fails with the usage status, naming the path."""
        code, out, err = _run(tmp_path / "no-such-env", capsys)
        combined = out + err

        assert code == 2
        assert str(tmp_path / "no-such-env") in combined
        assert "MISSING:" not in combined

    def test_lists_all_missing_variables(self, tmp_path, capsys):
        """S2.1: every absent required variable is named in one run."""
        values = dict(COMPLETE_ENV)
        values.pop("BIMEMO_DEBUG")
        values.pop("BIMEMO_HOST")
        env_path = _write_env(tmp_path, values)

        code, out, _ = _run(env_path, capsys)

        assert code == 1
        assert "MISSING: BIMEMO_DEBUG" in out
        assert "MISSING: BIMEMO_HOST" in out
        for name, _ in values.items():
            assert f"MISSING: {name}" not in out

    def test_empty_value_counts_as_missing(self, tmp_path, capsys):
        """S2.2: a present variable with an empty value is reported missing."""
        values = dict(COMPLETE_ENV)
        values["BIMEMO_DEBUG"] = ""
        env_path = _write_env(tmp_path, values)

        code, out, _ = _run(env_path, capsys)

        assert code == 1
        assert "MISSING: BIMEMO_DEBUG" in out
        for name, _ in values.items():
            if name != "BIMEMO_DEBUG":
                assert f"MISSING: {name}" not in out

    def test_mcp_enabled_requires_token_file(self, tmp_path, capsys):
        """S3.1: MCP enabled without the token file fails, naming the variable."""
        values = dict(COMPLETE_ENV)
        values["BIMEMO_MCP_ENABLED"] = "true"
        env_path = _write_env(tmp_path, values)

        code, out, _ = _run(env_path, capsys)

        assert code == 1
        assert "MISSING: BIMEMO_MCP_TOKEN_FILE" in out
        for name, _ in COMPLETE_ENV.items():
            assert f"MISSING: {name}" not in out

    def test_mcp_disabled_does_not_require_token_file(self, tmp_path, capsys):
        """S3.2: MCP disabled needs no token file and the gate passes."""
        values = dict(COMPLETE_ENV)
        values["BIMEMO_MCP_ENABLED"] = "false"
        env_path = _write_env(tmp_path, values)

        code, out, _ = _run(env_path, capsys)

        assert code == 0
        assert "BIMEMO_MCP_TOKEN_FILE" not in out

    def test_values_never_appear_in_report(self, tmp_path, capsys):
        """S4.1: no variable value is echoed when the gate fails."""
        secret = "sk-supersecrettoken-42"
        values = dict(COMPLETE_ENV)
        values.pop("BIMEMO_SEARXNG_URL")
        values["BIMEMO_DEBUG"] = secret
        env_path = _write_env(tmp_path, values)

        code, out, err = _run(env_path, capsys)
        combined = out + err

        assert code == 1
        assert "MISSING: BIMEMO_SEARXNG_URL" in combined
        for value in values.values():
            assert value not in combined

    def test_required_table_matches_env_example(self):
        """R2: the script's required set equals the .env.example (REQUIRED) set."""
        assert set(module.REQUIRED_VARS) == _required_from_env_example()
