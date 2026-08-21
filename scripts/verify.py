#!/usr/bin/env python3
"""biMEMO pre-merge verification gate.

Usage:
    ./scripts/verify.py            # run all stages
    ./scripts/verify.py <stage>... # run only the named stages

Stages (default order):
    image     rebuild the bimemo image from the current tree
    python    ruff check + ruff format --check + pytest (disposable container)
    openapi   spec/code drift check (disposable container)
    frontend  npm ci + lint + build + unit tests (disposable container)
    security  gitleaks secret scan

The stages mirror the GitHub CI (frontend.yml, backend.yml, security.yml),
so a green local run means a green CI after the push. The python and
frontend stages run in throwaway containers built from the current tree;
nothing is written to the host tree and no running development stack is
restarted or modified.

Runs with the system python3 (standard library only, no venv needed).
Exit code: 0 only if every requested stage passes.
"""

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "docker" / "docker-compose.yml"
GITLEAKS_IMAGE = "ghcr.io/gitleaks/gitleaks:latest"

ALL_STAGES = ("image", "python", "openapi", "frontend", "security")


def note(message: str) -> None:
    print()
    print(f"==> {message}")


def sh(command: list[str]) -> None:
    """Run a command in the repo root; raise on a non-zero exit code."""
    print("$ " + " ".join(command))
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def compose_project() -> str:
    """The compose project name (e.g. 'docker' from docker/docker-compose.yml)."""
    output = subprocess.run(
        ["docker-compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(output)["name"]


def stage_image() -> None:
    note("[1/4] image: rebuilding the bimemo image from the current tree")
    sh(["docker-compose", "-f", str(COMPOSE_FILE), "build", "bimemo"])


def stage_python() -> None:
    """ruff + pytest in a disposable container.

    The repository is mounted at /repo (repo-root layout, so the ruff
    config and per-file-ignores resolve as designed), the first-party
    package root is /repo/app, and .env.example is provided at
    /app/.env.example for the env-validation test. Check scope
    (app/backend, tests, scripts) matches the project's ruff config.
    """
    note("[2/5] python: ruff check/format + pytest (disposable container)")
    image = f"{compose_project()}-bimemo:latest"

    sh(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{REPO_ROOT}:/repo",
            "-w",
            "/repo",
            "-e",
            "PYTHONPATH=/repo/app",
            image,
            "ruff",
            "check",
            "app/backend",
            "tests",
            "scripts",
        ]
    )
    sh(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{REPO_ROOT}:/repo",
            "-w",
            "/repo",
            "-e",
            "PYTHONPATH=/repo/app",
            image,
            "ruff",
            "format",
            "--check",
            "app/backend",
            "tests",
            "scripts",
        ]
    )
    sh(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{REPO_ROOT}:/repo",
            "-w",
            "/repo",
            "-v",
            f"{REPO_ROOT}/.env.example:/app/.env.example:ro",
            "-e",
            "PYTHONPATH=/repo/app",
            image,
            "python",
            "-m",
            "pytest",
            "tests/",
            "-q",
        ]
    )


def stage_openapi() -> None:
    """OpenAPI spec/code drift check in a disposable container.

    Builds the application without starting the runtime, generates the
    OpenAPI document it advertises, and compares it with the committed
    api/openapi.json; any drift fails the stage. Same container and
    environment as the pytest step.
    """
    note("[3/5] openapi: spec/code drift check (disposable container)")
    image = f"{compose_project()}-bimemo:latest"

    sh(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{REPO_ROOT}:/repo",
            "-w",
            "/repo",
            "-v",
            f"{REPO_ROOT}/.env.example:/app/.env.example:ro",
            "-e",
            "PYTHONPATH=/repo/app",
            "-e",
            "HOME=/tmp",
            image,
            "python",
            "scripts/openapi_check.py",
        ]
    )


def stage_frontend() -> None:
    """npm ci + lint + build + unit tests in a disposable container.

    Runs from the `frontend` build stage of the Dockerfile (node 22 plus
    the tree baked in during the image build), as root, against the
    image's own copy - nothing is written to the host tree, so host
    permission state cannot break the gate. The frontend stage is
    rebuilt first so the tested code matches the current tree (a cached
    no-op when nothing changed).
    """
    note("[4/5] frontend: npm ci + lint + build + unit tests (disposable container)")
    image = f"{compose_project()}-bimemo-frontend:latest"

    sh(
        [
            "docker",
            "build",
            "-f",
            "docker/Dockerfile",
            "--target",
            "frontend",
            "-t",
            image,
            ".",
        ]
    )
    sh(
        [
            "docker",
            "run",
            "--rm",
            "-u",
            "0:0",
            "-w",
            "/frontend",
            image,
            "sh",
            "-c",
            "npm ci && npm run lint && npm run build && npm run test",
        ]
    )


def stage_security() -> None:
    note("[5/5] security: gitleaks secret scan")
    sh(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{REPO_ROOT}:/repo",
            "-w",
            "/repo",
            GITLEAKS_IMAGE,
            "detect",
            "--source",
            ".",
            "--config",
            ".gitleaks.toml",
            "--redact",
            "--verbose",
        ]
    )


STAGES = {
    "image": stage_image,
    "python": stage_python,
    "openapi": stage_openapi,
    "frontend": stage_frontend,
    "security": stage_security,
}


def main(argv: list[str]) -> int:
    if not argv:
        stages = list(ALL_STAGES)
    else:
        unknown = [s for s in argv if s not in STAGES]
        if unknown:
            print(
                "verify: unknown stage(s): "
                + ", ".join(unknown)
                + f" (choose from: {', '.join(ALL_STAGES)})",
                file=sys.stderr,
            )
            return 2
        stages = argv

    note("verify: stages: " + " ".join(stages))
    results: list[str] = []
    for name in stages:
        try:
            STAGES[name]()
        except subprocess.CalledProcessError:
            results.append(f"{name}: FAILED")
            print()
            print(f"verify: stage {name} failed - stopping.", file=sys.stderr)
            break
        results.append(f"{name}: OK")

    print()
    print("verify summary:")
    for result in results:
        print(f"  {result}")
    return 0 if all(r.endswith("OK") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
