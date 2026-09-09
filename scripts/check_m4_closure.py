"""Deterministic scope, continuity, and receipt audit for closing M4."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
M3_COMMIT = "4f21e20ba96cc252d12e418d2594e477280e9f85"
EVIDENCE = ROOT / "docs" / "evidence" / "M4"


def git(*args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return completed.stdout.decode("utf-8")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def without_current_gate_state(text: str) -> str:
    start = text.index("## Current gate state")
    end = text.index("## Gate topology")
    return text[:start] + text[end:]


def changed_paths() -> set[str]:
    paths: set[str] = set()
    for line in git("status", "--porcelain=v1").splitlines():
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.add(path.replace("\\", "/"))
    return paths


def allowed_m4_path(path: str) -> bool:
    exact = {
        "BUILD_STATE.md",
        "ROADMAP.md",
        "aircheck/agent/authority_hook.py",
        "aircheck/agent/diagnosis.py",
        "aircheck/authority/policy.py",
        "aircheck/domain/planning.py",
        "aircheck/domain/terminal.py",
        "aircheck/persistence/store.py",
        "aircheck/tools/contracts.py",
        "scripts/check_m4_closure.py",
        "tests/persistence/test_store.py",
        "tests/tools/test_registry.py",
    }
    return path in exact or path.startswith(("aircheck/runtime/", "tests/runtime/", "docs/evidence/M4/"))


def main() -> int:
    check(git("rev-parse", "gate/M3^{}").strip() == M3_COMMIT, "gate/M3 resolves to the exact verified predecessor")
    paths = changed_paths()
    unexpected = sorted(path for path in paths if not allowed_m4_path(path))
    check(not unexpected, f"working changes are M4-only (unexpected={unexpected})")

    for protected in (
        "specifications",
        "synthetic",
        "aircheck/media",
        "aircheck/agent/interpretation.py",
        "aircheck/agent/planning.py",
        "aircheck/domain/admission.py",
        "apps",
        "ARCHITECTURE.md",
        "PRODUCT_CONTRACT.md",
        "DECISIONS.md",
        "docs/WORKER_CONTRACT.md",
    ):
        check(
            not git("diff", "--name-only", "gate/M3", "--", protected).strip(),
            f"predecessor/future surface unchanged: {protected}",
        )

    baseline_roadmap = git("show", "gate/M3:ROADMAP.md")
    current_roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    check(
        without_current_gate_state(baseline_roadmap)
        == without_current_gate_state(current_roadmap),
        "ROADMAP correction changes only current gate-state continuity",
    )
    check("| M3 | Closed GREEN |" in current_roadmap, "ROADMAP records M3 CLOSED GREEN")
    check("| M4 | Closed GREEN |" in current_roadmap, "ROADMAP records M4 CLOSED GREEN")
    check(
        "| M5 and M6 | Eligible; **not authorized** |" in current_roadmap,
        "eligible successor gates remain explicitly unauthorized",
    )

    build_state = (ROOT / "BUILD_STATE.md").read_text(encoding="utf-8")
    for marker in (
        "LAST_VERIFIED_GATE: M4",
        "ACTIVE_GATE: NONE — M4 CLOSED GREEN",
        "AUTHORIZED_SCOPE: M4 ONLY — CLOSED",
        "NEXT_GATE: M5 and M6 eligible — NOT AUTHORIZED",
        "FUTURE_GATES: LOCKED",
    ):
        check(marker in build_state, f"BUILD_STATE marker present: {marker}")

    required_receipts = {
        "README.md",
        "M4.0_environment.txt",
        "M4.1_recovery_review_resolution.md",
        "M4.2_hero_capture.json",
        "M4.3_focused_tests.txt",
        "M4.4_final_verify.txt",
        "M4_CLOSING_REPORT.md",
    }
    check(EVIDENCE.is_dir(), "M4 evidence directory exists")
    check(required_receipts <= {path.name for path in EVIDENCE.iterdir()}, "required M4 receipts exist")
    for path in EVIDENCE.iterdir():
        if path.is_file():
            check(path.read_bytes().endswith(b"\n"), f"receipt has final newline: {path.name}")

    hero = json.loads((EVIDENCE / "M4.2_hero_capture.json").read_text(encoding="utf-8"))
    check(hero["terminal"]["outcome"] == "DELIVERY_READY", "hero earns deterministic DELIVERY_READY")
    check(hero["originals_unchanged"] is True, "hero originals remain byte-identical")
    check(hero["diagnosis_tier_exposure"] == [[1], [2]], "S3 option exposure is tier-reduced deterministically")
    check(hero["counts"] == {
        "actions_completed": 5,
        "actions_started": 5,
        "authorized_remediations": 1,
        "autonomous_remediations": 4,
    }, "hero action counts and completion balance are exact")
    check(hero["requirements_with_provenance"] == hero["requirements_total"] == 15, "every hero requirement retains validated provenance")

    focused = (EVIDENCE / "M4.3_focused_tests.txt").read_text(encoding="utf-8")
    check("75 passed" in focused and "EXIT_CODE: 0" in focused, "focused M4 receipt is GREEN")
    complete = (EVIDENCE / "M4.4_final_verify.txt").read_text(encoding="utf-8")
    for marker in ("207 passed", "Tests  12 passed (12)", "Build complete", "EXIT_CODE: 0"):
        check(marker in complete, f"complete verification receipt contains: {marker}")

    tests_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "tests" / "runtime").glob("test_*.py")
    ) + (ROOT / "tests" / "persistence" / "test_store.py").read_text(
        encoding="utf-8"
    ) + (ROOT / "tests" / "authority" / "test_policy.py").read_text(
        encoding="utf-8"
    )
    required_regressions = (
        "test_authorization_consume_and_action_start_recover_as_one_transaction",
        "test_transition_event_and_snapshot_reconcile_after_interruption",
        "test_post_promotion_store_failure_quarantines_uncommitted_output",
        "test_recovery_refuses_corrupt_persisted_successor_instead_of_false_completion",
        "test_failed_tier_two_attempt_voids_authorization_and_replay_starts_nothing",
        "test_terminal_restart_restores_missing_evidence_idempotently",
        "test_corrupt_runtime_manifest_is_not_silently_refreshed_after_derivative",
        "test_stricter_report_only_disposition_cannot_gain_an_action_option",
        "test_model_cannot_apply_tier_two_or_create_a_side_effect",
        "test_two_failed_safe_attempts_become_deterministically_blocked",
        "test_success_persists_successor_before_completed_event_and_preserves_original",
        "test_authority_rejects_inputs_outside_the_current_run",
        "test_original_targets_are_forbidden_for_every_mutating_tier",
        "test_authorization_is_single_use_and_exactly_bound",
        "test_strands_before_tool_call_adapter_emits_raw_typed_interrupt",
    )
    for name in required_regressions:
        check(name in tests_text, f"audit regression present: {name}")

    public_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in EVIDENCE.iterdir()
        if path.is_file()
    )
    forbidden_patterns = {
        "opaque authorization token": r"auth_[A-Za-z0-9_-]{16,}",
        "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "Google API key": r"AIza[0-9A-Za-z_-]{30,}",
        "AWS access key": r"AKIA[0-9A-Z]{16}",
        "hidden reasoning payload": r"(?i)<analysis>|reasoning_content",
        "absolute Windows path": r"[A-Za-z]:\\",
    }
    for label, pattern in forbidden_patterns.items():
        check(re.search(pattern, public_text) is None, f"public M4 evidence excludes {label}")

    subprocess.run(("git", "diff", "--check"), cwd=ROOT, check=True)
    check(True, "git diff --check passes")
    print("M4_CLOSURE_AUDIT: GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
