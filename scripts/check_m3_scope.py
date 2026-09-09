"""Fail closed unless the in-flight M3 tree satisfies its closure contract."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE = "gate/M2"
EXPECTED_TAGS = {
    "gate/M1": "74282b0c41b82382074df7329cafe6bebdf7331f",
    "gate/M2": "b41fc8e9eade81ab2e61f89d7b2e68b75d4dabb4",
    "gate/M3a": "4c75e00d1424c6d1b845651cc11920eb0f6fb893",
}
EXPECTED_RECEIPT_HASHES = {
    "M3.2_semantic_evaluation.json": "f89c121c2691253661a85ccd70e86ecad58b24e92933a005926db7ab6de8d94f",
    "M3.3_semantic_evaluation_final.json": "095e6bf25771eb18d57d984fbf205ea4b5aeb346803c58e6b8b1c48c00ebab31",
    "M3.4_semantic_evaluation_green.json": "971dbecda3eb012037c6a0f2dc65764b943792c0b91edd8f1cd226e7da8f19f0",
}
ALLOWED_EXACT = {
    "ARCHITECTURE.md",
    "BUILD_STATE.md",
    "README.md",
    "aircheck/domain/admission.py",
    "aircheck/domain/constraints.py",
    "aircheck/domain/planning.py",
    "pyproject.toml",
    "scripts/check_m3_scope.py",
    "tests/domain/test_admission.py",
    "tests/domain/test_planning.py",
}
ALLOWED_PREFIXES = (
    "aircheck/agent/",
    "docs/evidence/M3/",
    "docs/superpowers/plans/2026-09-07-m3-",
    "evals/runners/",
    "tests/agent/",
    "tests/evals/",
)
FROZEN_M2_PATHS = (
    "evals/expected",
    "evals/specs",
    "specifications/northstar/profiles/m2_product_profiles.json",
    "specifications/northstar/source",
    "synthetic/source/m2_expected_hashes.json",
)
SECRET_PATTERNS = (
    re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?i)(?:access_token|refresh_token|client_secret)\s*[:=]"),
    re.compile(rb"(?i)authorization\s*:\s*bearer\s+"),
)
HIDDEN_REASONING_PATTERNS = (
    re.compile(rb"(?i)reasoning_content"),
    re.compile(rb"(?i)thought_signature"),
    re.compile(rb"(?i)<\/?thinking>"),
    re.compile(rb'(?i)"reason"\s*:'),
)
PATH_PATTERN = re.compile(rb"(?:[A-Za-z]:\\|/(?:home|Users|workspace)/)")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def _changed_files() -> tuple[str, ...]:
    changed = _git("diff", "--name-only", BASE, "--")
    untracked = _git("ls-files", "--others", "--exclude-standard")
    if changed.returncode or untracked.returncode:
        raise RuntimeError("could not enumerate the M3 working tree")
    return tuple(
        sorted(
            {
                path.strip().replace("\\", "/")
                for output in (changed.stdout, untracked.stdout)
                for path in output.splitlines()
                if path.strip()
            }
        )
    )


def _schema_property_names(value: Any) -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            found.extend(str(name) for name in properties)
        for item in value.values():
            found.extend(_schema_property_names(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_schema_property_names(item))
    return tuple(found)


def _print_check(name: str, passed: bool, failures: list[str], detail: str = "") -> None:
    print(f"{name}: {'PASS' if passed else 'FAIL'}{detail}")
    if not passed:
        failures.append(name)


def main() -> int:
    failures: list[str] = []

    observed_tags = {
        tag: _git("rev-parse", "--verify", f"{tag}^{{commit}}").stdout.strip()
        for tag in EXPECTED_TAGS
    }
    _print_check(
        "PREDECESSOR_TAGS",
        observed_tags == EXPECTED_TAGS,
        failures,
        f" observed={observed_tags}",
    )
    _print_check(
        "HEAD_IS_GATE_M2_BEFORE_CLOSE",
        _git("rev-parse", "HEAD").stdout.strip() == EXPECTED_TAGS[BASE],
        failures,
    )

    changed_files = _changed_files()
    scope_violations = tuple(
        path
        for path in changed_files
        if path not in ALLOWED_EXACT
        and not any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES)
    )
    _print_check(
        "M3_ONLY_PATH_SCOPE",
        not scope_violations,
        failures,
        f" changed={len(changed_files)} violations={scope_violations}",
    )

    m2_diff = _git("diff", "--exit-code", BASE, "--", *FROZEN_M2_PATHS)
    _print_check("FROZEN_M2_GROUND_TRUTH", m2_diff.returncode == 0, failures)

    evidence_root = ROOT / "docs" / "evidence" / "M3"
    receipt_hashes = {
        name: hashlib.sha256((evidence_root / name).read_bytes()).hexdigest()
        for name in EXPECTED_RECEIPT_HASHES
    }
    _print_check(
        "SEMANTIC_RECEIPT_HASHES",
        receipt_hashes == EXPECTED_RECEIPT_HASHES,
        failures,
        f" observed={receipt_hashes}",
    )
    receipts = {
        name: json.loads((evidence_root / name).read_text(encoding="utf-8"))
        for name in EXPECTED_RECEIPT_HASHES
    }
    _print_check(
        "PRESERVED_RED_RECEIPTS",
        receipts["M3.2_semantic_evaluation.json"]["accepted"] is False
        and receipts["M3.3_semantic_evaluation_final.json"]["accepted"] is False,
        failures,
    )

    green = receipts["M3.4_semantic_evaluation_green.json"]
    expected_provider = {
        "path": "strands.models.gemini.GeminiModel",
        "model_id": "gemini-2.5-flash",
        "vertex_ai": True,
        "temperature": 0,
    }
    profiles = {item["profile_id"]: item for item in green["product_profiles"]}
    expected_counts = {
        "northstar_broadcast_master_v1": 15,
        "northstar_digital_preview_v1": 13,
    }
    profile_ok = set(profiles) == set(expected_counts)
    if profile_ok:
        for profile_id, count in expected_counts.items():
            profile = profiles[profile_id]
            profile_ok = profile_ok and len(profile["runs"]) == 3
            profile_ok = profile_ok and all(
                run["accepted"] is True and run["candidate_count"] == count
                for run in profile["runs"]
            )
            profile_ok = profile_ok and profile["plan"]["required_check_count"] == count
            profile_ok = profile_ok and len(profile["plan"]["items"]) >= count
    adversarial = green["adversarial_inputs"]
    semantic_ok = (
        green["accepted"] is True
        and green["provider"] == expected_provider
        and green["profile_runs"] == 3
        and green["distinct_product_plans"] is True
        and green["terminal_behavior"] == "NOT_EVALUATED_M3"
        and profile_ok
        and len(adversarial) == 7
        and all(
            item["accepted"] is True and item["terminal_evaluated"] is False
            for item in adversarial
        )
    )
    _print_check("ACCEPTED_TYPED_SEMANTIC_MATRIX", semantic_ok, failures)

    from aircheck.agent.contracts import CandidateRequirementBatch
    from aircheck.domain.planning import PlanProposal

    boundary_names = set(
        _schema_property_names(CandidateRequirementBatch.model_json_schema())
        + _schema_property_names(PlanProposal.model_json_schema())
    )
    forbidden_fields = boundary_names & {
        "action",
        "authorization",
        "end",
        "filesystem_path",
        "offset",
        "path",
        "start",
        "terminal_verdict",
    }
    _print_check(
        "MODEL_SCHEMA_HAS_NO_AUTHORITY_OR_PATH_FIELDS",
        not forbidden_fields,
        failures,
        f" forbidden={tuple(sorted(forbidden_fields))}",
    )

    semantic_json = b"\n".join(
        (evidence_root / name).read_bytes() for name in EXPECTED_RECEIPT_HASHES
    )
    public_bytes = b"\n".join(
        path.read_bytes() for path in evidence_root.iterdir() if path.is_file()
    )
    secret_matches = tuple(
        pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(public_bytes)
    )
    hidden_matches = tuple(
        pattern.pattern
        for pattern in HIDDEN_REASONING_PATTERNS
        if pattern.search(public_bytes)
    )
    _print_check(
        "PUBLIC_ARTIFACT_SECRET_SCAN",
        not secret_matches,
        failures,
        f" matches={secret_matches}",
    )
    _print_check(
        "NO_HIDDEN_REASONING_FIELDS_OR_TAGS",
        not hidden_matches,
        failures,
        f" matches={hidden_matches}",
    )
    _print_check(
        "SEMANTIC_RECEIPTS_HAVE_NO_FILESYSTEM_PATHS",
        PATH_PATTERN.search(semantic_json) is None,
        failures,
    )

    focused = (evidence_root / "M3.6_focused_tests_closure.txt").read_text(
        encoding="utf-8"
    )
    final_verify = (evidence_root / "M3.7_final_verify.txt").read_text(
        encoding="utf-8"
    )
    _print_check(
        "FOCUSED_TEST_RECEIPT_GREEN",
        "84 passed" in focused and "--- EXIT CODE: 0 ---" in focused,
        failures,
    )
    _print_check(
        "COMPLETE_REPOSITORY_VERIFY_GREEN",
        "--- EXIT CODE: 0 ---" in final_verify,
        failures,
    )

    whitespace = _git("diff", "--check")
    _print_check("GIT_DIFF_CHECK", whitespace.returncode == 0, failures)
    text_paths = tuple(
        ROOT / path
        for path in changed_files
        if (ROOT / path).is_file()
        and (ROOT / path).suffix
        in {".json", ".md", ".py", ".toml", ".txt"}
    )
    text_violations: list[str] = []
    for path in text_paths:
        content = path.read_bytes()
        if content and not content.endswith(b"\n"):
            text_violations.append(f"{path.relative_to(ROOT)}:missing-eof-newline")
        for line_number, line in enumerate(content.splitlines(), start=1):
            if line.rstrip(b" \t") != line:
                text_violations.append(
                    f"{path.relative_to(ROOT)}:{line_number}:trailing-whitespace"
                )
    _print_check(
        "EOF_AND_TRAILING_WHITESPACE",
        not text_violations,
        failures,
        f" violations={tuple(text_violations)}",
    )

    dependency_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dependency_ok = all(
        item in dependency_text
        for item in (
            '"strands-agents[gemini]==1.54.0"',
            '"google-genai==2.22.0"',
            '"google-auth==2.57.1"',
        )
    )
    _print_check("EXACT_M3A_PROVIDER_DEPENDENCIES", dependency_ok, failures)

    print("failures", tuple(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
