"""AIRCheck M6 evaluation corpus harness.

Runs the frozen deterministic end-to-end scenario corpus, grades every stage by
typed comparison against ground truth that is frozen *before the post-audit
authoritative rerun*,
attributes each failure to its first divergent stage, and emits machine-readable
results plus a rendered results table.

Usage:
    python -m evals.run_corpus --freeze-audit   # prove expected exists pre-run
    python -m evals.run_corpus                   # run corpus, write results
    python -m evals.run_corpus --results-dir DIR # override output directory

Doctrine: prose -> predicates -> facts -> authority -> evidence. There is no LLM
judge. The stochastic model is measured (predecessor M3 evidence), never trusted
to grade itself; this corpus grades the deterministic runtime with a typed
semantic double so identical input yields one expected result.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.corpus import CORPUS_VERSION  # noqa: E402
from evals.corpus import fixtures, scenarios  # noqa: E402
from evals.corpus.scenarios import SCENARIOS  # noqa: E402
from evals.corpus.stages import (  # noqa: E402
    STAGE_ORDER,
    extract_observed,
    first_divergence,
    observed_terminal,
)

EXPECTED_DIR = ROOT / "evals" / "corpus" / "expected"
DEFAULT_RESULTS_DIR = ROOT / "docs" / "evidence" / "M6"
REQUIRED_EXPECTED_KEYS = {
    "scenario_id",
    "corpus_version",
    "title",
    "family",
    "mission_ref",
    "expected_terminal",
    "stages",
}
VALID_TERMINALS = {"DELIVERY_READY", "BLOCKED", "FAILED", "REFUSED_SAFE"}

# Scenario-appropriate complete stage schemas. Every scenario must grade at least
# these stages for its family; an empty or under-specified `stages` object is
# rejected so a meaningless scenario cannot pass.
# Every scenario has a lifecycle ledger (events) and measurement records (empty for
# non-executable scenarios), so both are required for every family except the
# two-run comparison meta-scenario, whose material fact is the divergence.
REQUIRED_STAGES_BY_FAMILY: dict[str, set[str]] = {
    "product": {
        "ingestion", "interpretation", "admission", "applicability", "planning",
        "measurement", "predicate", "findings", "remediation", "events", "terminal",
        "evidence_integrity",
    },
    "isolated_defect": {
        "ingestion", "interpretation", "admission", "applicability", "planning",
        "measurement", "predicate", "findings", "remediation", "events", "terminal",
        "evidence_integrity",
    },
    "comparison": {"profile_divergence", "terminal"},
    "adversarial": {
        "interpretation", "admission", "applicability", "planning", "measurement",
        "predicate", "findings", "events", "terminal",
    },
    "conditional": {
        "interpretation", "admission", "applicability", "planning", "measurement",
        "predicate", "findings", "events", "terminal",
    },
    "authority": {
        "ingestion", "interpretation", "admission", "applicability", "planning",
        "measurement", "predicate", "findings", "authority", "remediation", "events",
        "terminal",
    },
    "refusal": {
        "interpretation", "admission", "applicability", "planning", "measurement",
        "findings", "authority", "remediation", "events", "terminal",
    },
    "unfixable_defect": {
        "interpretation", "admission", "applicability", "planning", "measurement",
        "predicate", "findings", "events", "terminal",
    },
    "integrity": {
        "interpretation", "admission", "applicability", "planning", "measurement",
        "predicate", "findings", "events", "terminal",
    },
    "recovery": {
        "interpretation", "admission", "applicability", "planning", "measurement",
        "predicate", "findings", "remediation", "events", "recovery", "terminal",
    },
}


# Material fields that must be present within each declared stage, so an expected
# artifact cannot weaken grading by deleting a graded field. terminal additionally
# requires outcome/counts/reason_codes when it declares has_verdict True (below).
REQUIRED_FIELDS_BY_STAGE: dict[str, set[str]] = {
    "ingestion": {"source_present", "original_asset_count"},
    "interpretation": {"requirement_count"},
    "admission": {"requirements", "status_reasons", "normalization_status_multiset", "severity_multiset"},
    "applicability": {"applicability_status_multiset"},
    "planning": {"required_check_count", "plan_items"},
    "measurement": {"records"},
    "predicate": {"final_by_key", "fail_keys_by_cycle", "pass_count", "fail_count", "not_evaluated_count", "bindings"},
    "findings": {"by_key", "requirement_keys", "final_blocking_keys"},
    "diagnosis": {"tier_exposure"},
    "authority": {"decision_request_count", "decision_request_statuses", "decision_choices"},
    "remediation": {
        "autonomous_remediations", "authorized_remediations", "action_started_count",
        "action_completed_count", "action_failed_count", "action_tools",
    },
    "events": {"skeleton"},
    "recovery": {"inspection_passes", "recovered_action_count", "recovery_reasons"},
    "terminal": {"terminal_status", "has_verdict"},
    "evidence_integrity": {"artifacts", "artifact_hashes_valid", "originals_integrity_verified"},
    "profile_divergence": {
        "broadcast_check_count", "preview_check_count", "distinct_check_counts",
        "broadcast_terminal", "preview_terminal", "broadcast_decision_count",
        "preview_decision_count",
    },
}


def exit_code_for(results: dict[str, Any]) -> int:
    """Falsifying exit code: nonzero for ANY harness error, FAIL, or shortfall."""

    totals = results["totals"]
    if totals["errored"] > 0:
        return 3
    if totals["failed"] > 0:
        return 1
    if totals["passed"] != totals["scenarios"]:
        return 2
    return 0


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, check=False, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _git_sha() -> str:
    return _git("rev-parse", "HEAD") or "UNKNOWN"


def _git_dirty() -> bool:
    return bool(_git("status", "--porcelain"))


def _sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def _expected_path(scenario_id: str) -> Path:
    return EXPECTED_DIR / f"{scenario_id}.json"


def _load_expected(scenario_id: str) -> tuple[dict[str, Any], str]:
    path = _expected_path(scenario_id)
    raw = path.read_bytes()
    expected = json.loads(raw)
    return expected, _sha256_bytes(raw)


def validate_expected(scenario_id: str, expected: dict[str, Any]) -> tuple[str, ...]:
    """Return schema errors for one expected artifact (empty when valid)."""

    errors: list[str] = []
    missing = REQUIRED_EXPECTED_KEYS - set(expected)
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
    if expected.get("scenario_id") != scenario_id:
        errors.append(
            f"scenario_id mismatch: {expected.get('scenario_id')!r} != {scenario_id!r}"
        )
    if expected.get("corpus_version") != CORPUS_VERSION:
        errors.append(f"corpus_version must be {CORPUS_VERSION!r}")
    if expected.get("expected_terminal") not in VALID_TERMINALS:
        errors.append(f"expected_terminal must be one of {sorted(VALID_TERMINALS)}")
    stages = expected.get("stages")
    if not isinstance(stages, dict):
        errors.append("stages must be an object")
    elif not stages:
        errors.append("stages must not be empty (an empty stages object cannot pass)")
    else:
        for stage, fields in stages.items():
            if stage not in STAGE_ORDER:
                errors.append(f"unknown stage: {stage}")
            if not isinstance(fields, dict):
                errors.append(f"stage {stage} must map to an object")
            elif not fields:
                errors.append(f"stage {stage} must declare at least one graded field")
            else:
                missing_fields = REQUIRED_FIELDS_BY_STAGE.get(stage, set()) - set(fields)
                if missing_fields:
                    errors.append(
                        f"stage {stage} missing required fields: {sorted(missing_fields)}"
                    )
                if stage == "terminal" and fields.get("has_verdict") is True:
                    verdict_fields = {"outcome", "counts", "reason_codes"} - set(fields)
                    if verdict_fields:
                        errors.append(
                            f"terminal with has_verdict requires: {sorted(verdict_fields)}"
                        )
                if stage == "admission" and expected.get("family") == "adversarial":
                    adversarial_fields = {
                        "adversarial_dimensions_pass", "adversarial_reason_codes",
                    } - set(fields)
                    if adversarial_fields:
                        errors.append(
                            f"adversarial admission requires: {sorted(adversarial_fields)}"
                        )
        family = expected.get("family")
        required = REQUIRED_STAGES_BY_FAMILY.get(family)
        if required is None:
            errors.append(f"unknown family {family!r}: cannot enforce complete stages")
        else:
            missing_stages = required - set(stages)
            if missing_stages:
                errors.append(
                    f"family {family!r} requires complete stages; missing: {sorted(missing_stages)}"
                )
    return tuple(errors)


def freeze_audit() -> dict[str, Any]:
    """Prove every scenario has a schema-valid frozen expected artifact."""

    entries: list[dict[str, Any]] = []
    ok = True
    for scenario in SCENARIOS:
        path = _expected_path(scenario.id)
        entry: dict[str, Any] = {"scenario_id": scenario.id, "mission_ref": scenario.mission_ref}
        if not path.exists():
            entry.update(exists=False, schema_valid=False, errors=["expected artifact absent"])
            ok = False
        else:
            expected, digest = _load_expected(scenario.id)
            errors = validate_expected(scenario.id, expected)
            entry.update(
                exists=True,
                sha256=digest,
                schema_valid=not errors,
                expected_terminal=expected.get("expected_terminal"),
                graded_stages=sorted(expected.get("stages", {})),
                errors=list(errors),
            )
            ok = ok and not errors
        entries.append(entry)
    stray = sorted(
        path.name
        for path in EXPECTED_DIR.glob("*.json")
        if path.stem not in {scenario.id for scenario in SCENARIOS}
    )
    results_exist = (DEFAULT_RESULTS_DIR / "results.json").exists()
    return {
        "corpus_version": CORPUS_VERSION,
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "audited_at": datetime.now(UTC).isoformat(),
        "scenario_count": len(SCENARIOS),
        "all_frozen_and_valid": ok and not stray,
        "stray_expected_files": stray,
        "prior_results_present": results_exist,
        "scenarios": entries,
    }


def _grade_scenario(scenario, sources, base: Path) -> dict[str, Any]:
    expected, digest = _load_expected(scenario.id)
    schema_errors = validate_expected(scenario.id, expected)
    record: dict[str, Any] = {
        "scenario_id": scenario.id,
        "title": scenario.title,
        "family": scenario.family,
        "mission_ref": scenario.mission_ref,
        "expected_artifact_sha256": digest,
        "expected_terminal": expected.get("expected_terminal"),
        "provider_run": False,
        "run_count": 1,
    }
    if schema_errors:
        record.update(
            eval_result="ERROR",
            error_class="EXPECTED_SCHEMA_INVALID",
            error_detail="; ".join(schema_errors),
            actual_terminal=None,
            first_divergence=None,
        )
        return record

    scenario_root = base / scenario.id
    scenario_root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        outcome = scenario.runner(scenario_root, sources)
        observed = extract_observed(outcome)
        actual_terminal = observed_terminal(outcome)
        stage, fields = first_divergence(expected["stages"], observed)
        terminal_ok = actual_terminal == expected["expected_terminal"]
        passed = stage is None and terminal_ok
        record["duration_s"] = round(time.perf_counter() - started, 3)
        record["actual_terminal"] = actual_terminal
        if not terminal_ok and stage is None:
            stage = "terminal"
            fields = ("terminal_status",)
        record["first_divergence"] = stage
        record["divergence_fields"] = list(fields)
        record["eval_result"] = "PASS" if passed else "FAIL"
        record["error_class"] = None
        record["difference_summary"] = _difference_summary(
            expected, observed, stage, fields, actual_terminal
        )
        record["observed_highlights"] = _highlights(observed)
    except Exception as exc:  # noqa: BLE001 - harness must fail loudly, not silently
        record.update(
            duration_s=round(time.perf_counter() - started, 3),
            eval_result="ERROR",
            error_class="HARNESS_ERROR",
            error_detail=f"{type(exc).__name__}: {exc}",
            error_traceback=traceback.format_exc().splitlines()[-6:],
            actual_terminal=None,
            first_divergence=None,
        )
    return record


def _difference_summary(
    expected: dict[str, Any],
    observed: dict[str, Any],
    stage: str | None,
    fields: tuple[str, ...],
    actual_terminal: str,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "expected_terminal": expected["expected_terminal"],
        "actual_terminal": actual_terminal,
    }
    if stage is None:
        return summary
    stage_expected = expected["stages"].get(stage, {})
    stage_observed = observed.get(stage, {})
    summary["stage"] = stage
    summary["fields"] = {
        field: {
            "expected": stage_expected.get(field),
            "observed": stage_observed.get(field),
        }
        for field in fields
        if field in stage_expected or field in stage_observed
    }
    return summary


def _highlights(observed: dict[str, Any]) -> dict[str, Any]:
    return {
        "terminal": observed.get("terminal", {}).get("terminal_status"),
        "required_checks": observed.get("planning", {}).get("required_check_count"),
        "autonomous_remediations": observed.get("remediation", {}).get("autonomous_remediations"),
        "authorized_remediations": observed.get("remediation", {}).get("authorized_remediations"),
        "final_blocking_keys": observed.get("findings", {}).get("final_blocking_keys"),
    }


def run_corpus(base: Path) -> dict[str, Any]:
    """Execute the full corpus and return the machine-readable results object."""

    sources = fixtures.build_sources(base / "_universe")
    records = [
        _grade_scenario(scenario, sources, base)
        for scenario in sorted(SCENARIOS, key=lambda item: item.id)
    ]
    passed = sum(1 for r in records if r["eval_result"] == "PASS")
    failed = sum(1 for r in records if r["eval_result"] == "FAIL")
    errored = sum(1 for r in records if r["eval_result"] == "ERROR")
    divergence_counts: dict[str, int] = {}
    for record in records:
        if record["eval_result"] == "FAIL" and record.get("first_divergence"):
            stage = record["first_divergence"]
            divergence_counts[stage] = divergence_counts.get(stage, 0) + 1
    terminal_expectations = {
        r["scenario_id"]: (r["expected_terminal"], r.get("actual_terminal"))
        for r in records
    }
    terminal_safe = sum(
        1
        for expected_t, actual_t in terminal_expectations.values()
        if actual_t is not None and expected_t == actual_t
    )
    runtime_terminals = {"DELIVERY_READY", "BLOCKED", "FAILED"}
    runtime_terminal_total = sum(
        1 for e, _ in terminal_expectations.values() if e in runtime_terminals
    )
    runtime_terminal_matched = sum(
        1 for e, a in terminal_expectations.values()
        if e in runtime_terminals and a == e
    )
    safe_refusal_total = sum(
        1 for e, _ in terminal_expectations.values() if e == "REFUSED_SAFE"
    )
    safe_refusal_matched = sum(
        1 for e, a in terminal_expectations.values() if e == "REFUSED_SAFE" and a == e
    )
    expected_snapshot = {
        "commit": _git_sha(),
        "dirty": _git_dirty(),
        "note": (
            "Expected artifacts were immutably frozen before this post-audit "
            "authoritative rerun; each row records the exact expected-artifact hash used."
        ),
        "artifacts": {
            record["scenario_id"]: record["expected_artifact_sha256"]
            for record in records
            if "expected_artifact_sha256" in record
        },
    }
    return {
        "corpus_version": CORPUS_VERSION,
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "generated_at": datetime.now(UTC).isoformat(),
        "expected_snapshot": expected_snapshot,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "ffmpeg": _ffmpeg_version(),
            "grading": "deterministic typed comparison; no LLM judge",
            "semantic_boundary": "deterministic typed double (CorpusSemanticModel)",
            "provider_runs": 0,
            "deterministic_runs": len(records),
            "stochastic_semantic_evidence": "predecessor M3 (Gemini 2.5 Flash) under docs/evidence/M3/",
        },
        "totals": {
            "scenarios": len(records),
            "passed": passed,
            "failed": failed,
            "errored": errored,
            "provider_runs": 0,
            "deterministic_runs": len(records),
            "terminal_outcome_matched": terminal_safe,
            "runtime_terminal_matched": runtime_terminal_matched,
            "runtime_terminal_total": runtime_terminal_total,
            "safe_refusal_matched": safe_refusal_matched,
            "safe_refusal_total": safe_refusal_total,
        },
        "first_divergence_by_stage": divergence_counts,
        "scenarios": records,
    }


def _ffmpeg_version() -> str:
    completed = subprocess.run(
        ["ffmpeg", "-version"], check=False, capture_output=True, text=True
    )
    if completed.returncode != 0:
        return "UNAVAILABLE"
    return completed.stdout.splitlines()[0] if completed.stdout else "UNKNOWN"


def render_results_md(results: dict[str, Any]) -> str:
    from evals.corpus.render import render_markdown

    return render_markdown(results)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AIRCheck M6 evaluation corpus.")
    parser.add_argument("--freeze-audit", action="store_true", help="only audit frozen expected artifacts")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--base-dir", type=Path, default=None, help="ephemeral run/workspace/store root")
    args = parser.parse_args(argv)

    if args.freeze_audit:
        audit = freeze_audit()
        text = json.dumps(audit, indent=2, sort_keys=True) + "\n"
        _write(args.results_dir / "M6.1_corpus_freeze.json", text)
        print(text)
        return 0 if audit["all_frozen_and_valid"] and not audit["prior_results_present"] else 1

    # Refuse to execute any scenario without a schema-valid frozen expected artifact.
    audit = freeze_audit()
    if not audit["all_frozen_and_valid"]:
        print("FROZEN EXPECTED AUDIT FAILED; refusing to execute the corpus.", file=sys.stderr)
        print(json.dumps(audit, indent=2, sort_keys=True), file=sys.stderr)
        return 2

    cleanup = args.base_dir is None
    base = Path(args.base_dir) if args.base_dir else Path(tempfile.mkdtemp(prefix="aircheck-m6-"))
    try:
        results = run_corpus(base)
    finally:
        if cleanup:
            import shutil

            shutil.rmtree(base, ignore_errors=True)

    results_json = json.dumps(results, indent=2, sort_keys=True) + "\n"
    _write(args.results_dir / "results.json", results_json)
    _write(args.results_dir / "M6.2_corpus_results.json", results_json)
    _write(args.results_dir / "RESULTS.md", render_results_md(results))

    totals = results["totals"]
    print(
        f"scenarios={totals['scenarios']} passed={totals['passed']} "
        f"failed={totals['failed']} errored={totals['errored']}"
    )
    print(f"first_divergence_by_stage={results['first_divergence_by_stage']}")
    code = exit_code_for(results)
    if code != 0:
        print(f"CORPUS GATE FAILED (exit {code}): closure requires passed==scenarios, 0 failed, 0 errored.", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
