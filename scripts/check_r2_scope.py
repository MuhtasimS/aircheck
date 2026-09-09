"""Fail closed if the R2 gate contains forbidden successor-gate work."""

from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = "gate/R1"
EXPECTED_PLAN_SHA256 = "c813fccfeca3a62ec9eacba2c9bf3a679c9fed60c68174378341737fca11defc"
EXPECTED_TOOLS = (
    "scan_package",
    "probe_media",
    "measure_loudness",
    "inspect_captions",
    "verify_manifest",
    "rename_delivery_copy",
    "write_checksum_manifest",
    "convert_caption_format",
    "create_normalized_audio_derivative",
)
FORBIDDEN_IMPORT_PREFIXES = (
    "boto3",
    "botocore",
    "fastapi",
    "google.genai",
    "strands",
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def _import_violations() -> tuple[str, ...]:
    violations: list[str] = []
    source_roots = (
        ROOT / "aircheck",
        ROOT / "apps" / "api",
        ROOT / "evals",
        ROOT / "synthetic",
    )
    for source_root in source_roots:
        for path in source_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: tuple[str, ...] = ()
                if isinstance(node, ast.Import):
                    names = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = (node.module,)
                for name in names:
                    if name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                        violations.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}:{name}"
                        )
    return tuple(violations)


def main() -> int:
    failures: list[str] = []
    base = _git("rev-parse", "--verify", BASE)
    if base.returncode != 0:
        failures.append("gate/R1 is missing")

    changed = _git("diff", "--name-only", BASE, "--")
    print("changed_files_since_gate_R1")
    print(changed.stdout.strip())
    if changed.returncode != 0:
        failures.append("could not inspect R2 diff")

    frontend = _git("diff", "--exit-code", BASE, "--", "apps/web")
    print("frontend_unchanged", frontend.returncode == 0)
    if frontend.returncode != 0:
        failures.append("apps/web changed during R2")

    plan = ROOT / "docs" / "evidence" / "R2" / "input" / "CONSOLIDATED_PLAN.md"
    plan_hash = hashlib.sha256(plan.read_bytes()).hexdigest()
    print("approved_plan_sha256", plan_hash)
    if plan_hash != EXPECTED_PLAN_SHA256:
        failures.append("approved plan input hash changed")

    from aircheck.tools import TOOL_REGISTRY
    from scripts.project import COMMANDS

    print("tool_inventory", tuple(TOOL_REGISTRY))
    print("runner_commands", COMMANDS)
    if tuple(TOOL_REGISTRY) != EXPECTED_TOOLS:
        failures.append("agent-facing tool inventory drifted")
    if "demo" in COMMANDS:
        failures.append("placeholder demo command exists")

    imports = _import_violations()
    print("forbidden_runtime_imports", imports)
    if imports:
        failures.append("future-gate runtime dependency imported")

    tags = _git("tag", "--list", "gate/M0").stdout.strip()
    print("historical_gate_M0_absent", not bool(tags))
    if tags:
        failures.append("historical gate/M0 was backfilled")

    required_docs = (
        "PRODUCT_CONTRACT.md",
        "ARCHITECTURE.md",
        "ROADMAP.md",
        "BUILD_STATE.md",
        "DECISIONS.md",
        "docs/WORKER_CONTRACT.md",
    )
    missing = tuple(path for path in required_docs if not (ROOT / path).is_file())
    print("missing_canonical_docs", missing)
    if missing:
        failures.append("canonical documents are missing")

    print("failures", tuple(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
