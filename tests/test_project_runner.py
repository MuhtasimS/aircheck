from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "project.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("aircheck_project_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_help_exposes_only_real_foundation_operations() -> None:
    completed = subprocess.run(
        [sys.executable, str(RUNNER), "--help"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    # The foundation surface, including the bounded local demo runner (M8: the
    # canonical `make install test demo` entrypoint).
    for command in ("install", "test", "lint", "build", "verify", "demo"):
        assert command in completed.stdout
    # No deployment/publication/submission commands leak into the local surface.
    for forbidden in ("deploy", "publish", "submit", "release"):
        assert forbidden not in completed.stdout.lower()


def test_runner_plan_is_cross_platform_and_never_uses_a_shell() -> None:
    runner = _load_runner()

    windows = runner.command_plan("verify", platform_name="nt", python="python.exe")
    linux = runner.command_plan("verify", platform_name="posix", python="python3")

    assert [step.label for step in windows] == [
        "backend tests",
        "frontend tests",
        "frontend build",
        "frontend lint",
        "whitespace check",
    ]
    assert windows[1].argv[0] == "npm.cmd"
    assert linux[1].argv[0] == "npm"
    assert windows[0].argv[:3] == ("python.exe", "-m", "pytest")
    assert linux[0].argv[:3] == ("python3", "-m", "pytest")
    assert all(step.shell is False for step in (*windows, *linux))
    assert all(step.cwd.is_absolute() for step in (*windows, *linux))


def test_runner_rejects_unknown_or_future_commands() -> None:
    # A genuinely out-of-scope command (deployment/publication is never a local
    # foundation operation) must be rejected by the runner's fixed choice set.
    completed = subprocess.run(
        [sys.executable, str(RUNNER), "deploy"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode != 0
    assert "invalid choice" in completed.stderr
