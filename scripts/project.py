"""Cross-platform AIRCheck foundation task runner."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "apps" / "web"
COMMANDS = ("install", "test", "lint", "build", "verify", "demo")


@dataclass(frozen=True)
class CommandStep:
    label: str
    argv: tuple[str, ...]
    cwd: Path
    shell: bool = False


def _npm_executable(platform_name: str) -> str:
    return "npm.cmd" if platform_name == "nt" else "npm"


def command_plan(
    command: str,
    *,
    platform_name: str = os.name,
    python: str = sys.executable,
) -> tuple[CommandStep, ...]:
    """Return the explicit subprocess plan for one foundation operation."""

    npm = _npm_executable(platform_name)
    install = (
        CommandStep(
            "backend install",
            (python, "-m", "pip", "install", "-e", ".[test]"),
            ROOT,
        ),
        CommandStep("frontend install", (npm, "ci"), WEB_ROOT),
    )
    tests = (
        CommandStep("backend tests", (python, "-m", "pytest", "-q"), ROOT),
        CommandStep("frontend tests", (npm, "test"), WEB_ROOT),
    )
    lint = (
        CommandStep("frontend lint", (npm, "run", "lint"), WEB_ROOT),
        CommandStep("whitespace check", ("git", "diff", "--check"), ROOT),
    )
    build = (CommandStep("frontend build", (npm, "run", "build"), WEB_ROOT),)
    demo = (
        CommandStep(
            "local demo",
            (python, str(ROOT / "scripts" / "demo.py")),
            ROOT,
        ),
    )

    plans = {
        "install": install,
        "test": tests,
        "lint": lint,
        "build": build,
        # Build before lint: the frontend lint/type-check needs the ambient route
        # types the production build generates, which are absent on a fresh clone
        # (they live under the git-ignored .vinext/). Building first keeps `verify`
        # reproducible on a cold checkout (M8 fresh-clone reproducibility).
        "verify": tests + build + lint,
        "demo": demo,
    }
    try:
        return plans[command]
    except KeyError as exc:
        raise ValueError(f"unknown project command: {command}") from exc


def run_plan(steps: Sequence[CommandStep]) -> int:
    for step in steps:
        shown = subprocess.list2cmdline(step.argv)
        print(f"\n==> {step.label}: {shown}", flush=True)
        completed = subprocess.run(
            step.argv,
            cwd=step.cwd,
            check=False,
            shell=step.shell,
        )
        if completed.returncode != 0:
            return completed.returncode
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run AIRCheck foundation install and verification operations.",
    )
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args(argv)
    return run_plan(command_plan(args.command))


if __name__ == "__main__":
    raise SystemExit(main())
