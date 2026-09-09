"""Falsifiability tests for the M6 corpus harness.

These prove the evaluator itself is falsifiable: each corruption class the
independent audit demonstrated (and several more) must be caught, attributed to
the earliest materially wrong stage, and must drive the gate command / closure
audit to a non-passing result. They also pin the corpus candidate batches to
canonical M2/M3 ground truth. They do not execute the media corpus.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

from aircheck.domain.source import ingest_source_document, segment_source_document
from aircheck.domain.types import SourceKind

from evals.corpus import CORPUS_VERSION, batches
from evals.corpus.scenarios import SCENARIOS, scenario_ids
from evals.corpus.stages import STAGE_ORDER, compare_stage, first_divergence
from evals import run_corpus
from evals.corpus import closure_audit

from tests.agent import fixtures as canonical

ROOT = Path(__file__).resolve().parents[2]
PROFILE_SOURCE = ROOT / "specifications" / "northstar" / "source"
SPEC_SOURCE = ROOT / "evals" / "specs"
EXPECTED_DIR = ROOT / "evals" / "corpus" / "expected"


def _stages(scenario_id: str) -> dict:
    return json.loads((EXPECTED_DIR / f"{scenario_id}.json").read_text(encoding="utf-8"))["stages"]


def _clean_pair(scenario_id: str) -> tuple[dict, dict]:
    """Return (frozen expected stages, an identical clean observed copy)."""

    expected = _stages(scenario_id)
    return expected, copy.deepcopy(expected)


# --- corpus shape and freeze ------------------------------------------------

def test_corpus_defines_unique_ordered_scenarios_covering_the_repaired_set() -> None:
    ids = scenario_ids()
    assert len(ids) == len(set(ids)) == 27
    assert list(ids) == sorted(ids)
    # The repair added genuine conditional grammar and interrupted-action recovery.
    assert "25_conditional_supported_applicable" in ids
    assert "26_conditional_reversed_unresolved" in ids
    assert "27_interrupted_action_recovery" in ids


def test_every_scenario_has_a_complete_schema_valid_frozen_expected_artifact() -> None:
    audit = run_corpus.freeze_audit()
    assert audit["all_frozen_and_valid"] is True, audit
    assert audit["stray_expected_files"] == []
    assert {e["scenario_id"] for e in audit["scenarios"]} == set(scenario_ids())


def test_clean_observed_matches_frozen_expected_at_every_stage() -> None:
    for scenario_id in scenario_ids():
        expected, observed = _clean_pair(scenario_id)
        stage, fields = first_divergence(expected, observed)
        assert stage is None, (scenario_id, stage, fields)


# --- schema strictness ------------------------------------------------------

def test_empty_stages_object_is_rejected() -> None:
    bad = {
        "scenario_id": "x", "corpus_version": CORPUS_VERSION, "title": "t",
        "family": "product", "mission_ref": "m", "expected_terminal": "BLOCKED",
        "stages": {},
    }
    assert any("empty" in e for e in run_corpus.validate_expected("x", bad))


def test_family_requires_complete_stages() -> None:
    # A product scenario that omits the predicate stage is rejected as incomplete.
    stages = {s: {"_": 1} for s in ("ingestion", "interpretation", "admission",
                                    "applicability", "planning", "findings",
                                    "remediation", "terminal", "evidence_integrity")}
    bad = {
        "scenario_id": "x", "corpus_version": CORPUS_VERSION, "title": "t",
        "family": "product", "mission_ref": "m", "expected_terminal": "DELIVERY_READY",
        "stages": stages,
    }
    errors = run_corpus.validate_expected("x", bad)
    assert any("predicate" in e and "missing" in e for e in errors)


def test_unknown_family_is_rejected() -> None:
    bad = {
        "scenario_id": "x", "corpus_version": CORPUS_VERSION, "title": "t",
        "family": "made_up", "mission_ref": "m", "expected_terminal": "BLOCKED",
        "stages": {"terminal": {"terminal_status": "BLOCKED"}},
    }
    assert any("unknown family" in e for e in run_corpus.validate_expected("x", bad))


# --- comparator corruption probes (the audit's demonstrated false-accepts) ---

def test_wrong_predicate_key_map_is_caught_at_predicate() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    # Flip a single predicate outcome while leaving counts/terminal untouched.
    observed["predicate"]["final_by_key"]["audio.true_peak"] = "FAIL"
    stage, fields = first_divergence(expected, observed)
    assert stage == "predicate"
    assert "final_by_key" in fields


def test_nonsensical_recovery_count_is_caught_at_recovery() -> None:
    expected, observed = _clean_pair("27_interrupted_action_recovery")
    observed["recovery"]["recovered_action_count"] = 99
    stage, fields = first_divergence(expected, observed)
    assert stage == "recovery"


def test_plan_binding_corruption_is_caught_at_planning() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    observed["planning"]["plan_items"]["video.codec"]["tool"] = "measure_loudness"
    stage, fields = first_divergence(expected, observed)
    assert stage == "planning"
    assert "plan_items" in fields


def test_earlier_cycle_overwrite_is_caught_even_when_final_is_correct() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    # Corrupt an earlier cycle's failure set; the final predicates stay correct.
    assert observed["predicate"]["fail_keys_by_cycle"], "scenario needs multiple cycles"
    observed["predicate"]["fail_keys_by_cycle"][0][1] = ["container.format"]
    # Final predicate map is untouched and still matches.
    assert observed["predicate"]["final_by_key"] == expected["predicate"]["final_by_key"]
    stage, fields = first_divergence(expected, observed)
    assert stage == "predicate"
    assert "fail_keys_by_cycle" in fields


def test_event_skeleton_corruption_is_caught_at_events() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    assert observed["events"]["skeleton"], "scenario needs an event skeleton"
    # Reverse the order: ACTION_COMPLETED must follow its ACTION_STARTED.
    observed["events"]["skeleton"] = list(reversed(observed["events"]["skeleton"]))
    stage, fields = first_divergence(expected, observed)
    assert stage == "events"


def test_requirement_severity_corruption_is_caught_at_admission() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    observed["admission"]["requirements"][0]["severity"] = "WARNING"
    stage, fields = first_divergence(expected, observed)
    assert stage == "admission"
    assert "requirements" in fields


def test_requirement_constraint_corruption_is_caught_at_admission() -> None:
    expected, observed = _clean_pair("01_clean_broadcast")
    for record in observed["admission"]["requirements"]:
        if record["measurement_key"] == "audio.integrated_loudness":
            record["constraint"]["lower"] = -99.0
    stage, fields = first_divergence(expected, observed)
    assert stage == "admission"


def test_extra_key_cannot_evade_exact_map_grading() -> None:
    expected, observed = _clean_pair("18_codec_defect")
    # Injecting an extra predicate the expected does not know about is a divergence.
    observed["predicate"]["final_by_key"]["video.width"] = "PASS"
    stage, fields = first_divergence(expected, observed)
    assert stage == "predicate"


def test_first_divergence_reports_earliest_stage_not_the_terminal() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    # Corrupt an early stage AND the terminal; attribution must be the early one.
    observed["admission"]["requirements"][0]["normalization_status"] = "AMBIGUOUS"
    observed["terminal"]["outcome"] = "BLOCKED"
    observed["terminal"]["terminal_status"] = "BLOCKED"
    stage, _ = first_divergence(expected, observed)
    assert stage == "admission"


def test_deleted_earlier_cycle_measurement_is_caught_at_measurement() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    records = observed["measurement"]["records"]
    cycle0 = [r for r in records if r["cycle"] == 0]
    assert cycle0, "scenario needs an earlier-cycle measurement"
    records.remove(cycle0[0])
    stage, fields = first_divergence(expected, observed)
    assert stage == "measurement"
    assert "records" in fields


def test_changed_measurement_value_with_same_status_is_caught() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    changed = False
    for record in observed["measurement"]["records"]:
        if record["measurement_key"] == "video.width" and record["status"] == "OK":
            record["value"] = 1280  # material value change; status stays OK
            changed = True
    assert changed
    stage, fields = first_divergence(expected, observed)
    assert stage == "measurement"
    assert "records" in fields


def test_wrong_measurement_unit_is_caught_at_measurement() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    for record in observed["measurement"]["records"]:
        if record["measurement_key"] == "video.width":
            record["unit"] = "inches"
    stage, _ = first_divergence(expected, observed)
    assert stage == "measurement"


def test_wrong_measurement_cycle_binding_is_caught_at_measurement() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    observed["measurement"]["records"][0]["cycle"] = 99
    stage, _ = first_divergence(expected, observed)
    assert stage == "measurement"


def _transition_indices(skeleton: list) -> list[int]:
    return [i for i, e in enumerate(skeleton) if e.get("type") == "STATE_TRANSITION"]


def test_deleted_lifecycle_transition_is_caught_at_events() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    skeleton = observed["events"]["skeleton"]
    indices = _transition_indices(skeleton)
    assert indices, "scenario needs lifecycle transitions in its skeleton"
    del skeleton[indices[0]]  # e.g. drop CREATED -> INGESTING
    stage, fields = first_divergence(expected, observed)
    assert stage == "events"
    assert "skeleton" in fields


def test_reordered_lifecycle_transition_is_caught_at_events() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    skeleton = observed["events"]["skeleton"]
    skeleton[0], skeleton[1] = skeleton[1], skeleton[0]
    stage, _ = first_divergence(expected, observed)
    assert stage == "events"


def test_duplicated_lifecycle_transition_is_caught_at_events() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    skeleton = observed["events"]["skeleton"]
    skeleton.insert(1, dict(skeleton[0]))  # invalid duplicate of the first transition
    stage, _ = first_divergence(expected, observed)
    assert stage == "events"


def test_removed_required_stage_fields_are_rejected_by_validation() -> None:
    # Removing a material field (not the whole stage) must be rejected so an expected
    # artifact cannot silently weaken grading.
    base = json.loads((EXPECTED_DIR / "06_hero_all_defects.json").read_text(encoding="utf-8"))

    planning_bad = copy.deepcopy(base)
    del planning_bad["stages"]["planning"]["plan_items"]
    assert any(
        "planning" in e and "plan_items" in e
        for e in run_corpus.validate_expected(base["scenario_id"], planning_bad)
    )

    predicate_bad = copy.deepcopy(base)
    del predicate_bad["stages"]["predicate"]["final_by_key"]
    assert any(
        "predicate" in e and "final_by_key" in e
        for e in run_corpus.validate_expected(base["scenario_id"], predicate_bad)
    )

    predicate_cycle_bad = copy.deepcopy(base)
    del predicate_cycle_bad["stages"]["predicate"]["fail_keys_by_cycle"]
    assert any(
        "fail_keys_by_cycle" in e
        for e in run_corpus.validate_expected(base["scenario_id"], predicate_cycle_bad)
    )

    measurement_bad = copy.deepcopy(base)
    del measurement_bad["stages"]["measurement"]["records"]
    assert run_corpus.validate_expected(base["scenario_id"], measurement_bad)

    events_bad = copy.deepcopy(base)
    del events_bad["stages"]["events"]["skeleton"]
    assert run_corpus.validate_expected(base["scenario_id"], events_bad)


def _first_of_type(skeleton: list, entry_type: str) -> dict:
    return next(e for e in skeleton if e.get("type") == entry_type)


# --- relationship-integrity probes (third audit) ----------------------------

def test_predicate_rebound_to_wrong_measurement_is_caught_at_predicate() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    bindings = observed["predicate"]["bindings"]
    binding = bindings[0]
    # Rebind to a DIFFERENT measurement that genuinely exists in this run: every
    # field stays individually valid; only the predicate->measurement pairing is wrong.
    other = next(b for b in bindings if b["bound_measurement_key"] != binding["bound_measurement_key"])
    binding["bound_measurement_key"] = other["bound_measurement_key"]
    binding["bound_requirement_key"] = other["bound_requirement_key"]
    stage, fields = first_divergence(expected, observed)
    assert stage == "predicate"
    assert "bindings" in fields


def test_predicate_bound_to_wrong_cycle_measurement_is_caught_at_predicate() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    bindings = observed["predicate"]["bindings"]
    binding = bindings[0]
    # Point at a real cycle that occurs in this run but is not this predicate's cycle.
    other_cycle = next(
        b["bound_cycle"] for b in bindings
        if b["bound_cycle"] is not None and b["bound_cycle"] != binding["bound_cycle"]
    )
    binding["bound_cycle"] = other_cycle
    stage, fields = first_divergence(expected, observed)
    assert stage == "predicate"
    assert "bindings" in fields


def test_predicate_referencing_missing_measurement_is_caught_at_predicate() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    binding = observed["predicate"]["bindings"][0]
    binding["bound_measurement_key"] = None
    binding["bound_requirement_key"] = None
    binding["bound_cycle"] = None
    stage, fields = first_divergence(expected, observed)
    assert stage == "predicate"


def test_event_envelope_payload_contradiction_is_caught_at_events() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    entry = _first_of_type(observed["events"]["skeleton"], "STATE_TRANSITION")
    entry["envelope_type"] = "ACTION_STARTED"  # envelope contradicts payload
    stage, fields = first_divergence(expected, observed)
    assert stage == "events"


def _other_start(skeleton: list, ordinal: int) -> dict:
    """A different ACTION_STARTED than the one carrying `ordinal` (real, not fabricated)."""
    return next(
        e for e in skeleton
        if e["type"] == "ACTION_STARTED" and e["action_ordinal"] != ordinal
    )


def test_action_completion_rebound_to_wrong_start_is_caught_at_events() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    sk = observed["events"]["skeleton"]
    entry = _first_of_type(sk, "ACTION_COMPLETED")
    other = _other_start(sk, entry["action_ordinal"])
    # Link this completion to a genuine but different start: both fields stay real.
    entry["action_ordinal"] = other["action_ordinal"]
    entry["tool"] = other["tool"]
    stage, _ = first_divergence(expected, observed)
    assert stage == "events"


def test_action_failure_rebound_to_wrong_start_is_caught_at_events() -> None:
    expected, observed = _clean_pair("23_remediation_failure_blocks")
    sk = observed["events"]["skeleton"]
    entry = _first_of_type(sk, "ACTION_FAILED")
    other = _other_start(sk, entry["action_ordinal"])
    entry["action_ordinal"] = other["action_ordinal"]
    entry["tool"] = other["tool"]
    stage, _ = first_divergence(expected, observed)
    assert stage == "events"


def test_action_completion_without_start_is_caught_at_events() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    entry = _first_of_type(observed["events"]["skeleton"], "ACTION_COMPLETED")
    entry["action_ordinal"] = None
    entry["tool"] = None
    stage, _ = first_divergence(expected, observed)
    assert stage == "events"


def test_action_tool_identity_mismatch_is_caught_at_events() -> None:
    expected, observed = _clean_pair("06_hero_all_defects")
    entry = _first_of_type(observed["events"]["skeleton"], "ACTION_COMPLETED")
    entry["tool"] = "create_normalized_audio_derivative" if entry["tool"] != "create_normalized_audio_derivative" else "rename_delivery_copy"
    stage, _ = first_divergence(expected, observed)
    assert stage == "events"


def test_removed_admission_status_reasons_is_rejected() -> None:
    base = json.loads((EXPECTED_DIR / "06_hero_all_defects.json").read_text(encoding="utf-8"))
    bad = copy.deepcopy(base)
    del bad["stages"]["admission"]["status_reasons"]
    assert any(
        "admission" in e and "status_reasons" in e
        for e in run_corpus.validate_expected(base["scenario_id"], bad)
    )


def test_adversarial_admission_requires_dimensions_and_reason_codes() -> None:
    base = json.loads((EXPECTED_DIR / "08_spec_ambiguous.json").read_text(encoding="utf-8"))
    bad = copy.deepcopy(base)
    del bad["stages"]["admission"]["adversarial_dimensions_pass"]
    assert any(
        "adversarial" in e for e in run_corpus.validate_expected(base["scenario_id"], bad)
    )


def test_ordered_vs_unordered_field_comparison() -> None:
    # tier_exposure order is material (a set would hide a reordered exposure).
    assert compare_stage({"tier_exposure": [[1], [2]]}, {"tier_exposure": [[2], [1]]})
    # requirement multisets are order-independent.
    a = [{"measurement_key": "x"}, {"measurement_key": "y"}]
    assert compare_stage({"requirements": a}, {"requirements": list(reversed(a))}) == ()


# --- falsifying gate / closure semantics ------------------------------------

def _totals(passed: int, failed: int, errored: int, scenarios: int = 27) -> dict:
    return {"totals": {"passed": passed, "failed": failed, "errored": errored, "scenarios": scenarios}}


def test_exit_code_is_zero_only_when_every_scenario_passes() -> None:
    assert run_corpus.exit_code_for(_totals(27, 0, 0)) == 0
    assert run_corpus.exit_code_for(_totals(26, 1, 0)) == 1   # a FAIL is nonzero
    assert run_corpus.exit_code_for(_totals(26, 0, 1)) == 3   # a harness ERROR is nonzero
    assert run_corpus.exit_code_for(_totals(26, 0, 0)) == 2   # a shortfall is nonzero


def test_closure_is_red_unless_every_scenario_passes() -> None:
    assert closure_audit.closure_ok(_totals(27, 0, 0))[0] is True
    assert closure_audit.closure_ok(_totals(26, 1, 0))[0] is False
    assert closure_audit.closure_ok(_totals(26, 0, 1))[0] is False
    assert closure_audit.closure_ok(_totals(0, 0, 0, scenarios=27))[0] is False


# --- ground-truth drift guard ----------------------------------------------

def _segments(path: Path, run_id: str, kind: SourceKind):
    document = ingest_source_document(
        doc_id=f"doc_{run_id}", run_id=run_id, kind=kind, title="drift",
        raw=path.read_bytes(), ingested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    return segment_source_document(document)


def _strip_reason(batch: dict) -> dict:
    return {
        "requirements": [
            {k: v for k, v in candidate.items() if k != "reason"}
            for candidate in batch["requirements"]
        ]
    }


def test_corpus_batches_match_canonical_ground_truth() -> None:
    for profile_id, source in (
        ("northstar_broadcast_master_v1", "broadcast_master.md"),
        ("northstar_digital_preview_v1", "digital_preview.md"),
    ):
        segments = _segments(PROFILE_SOURCE / source, "run_drift", SourceKind.PROFILE)
        assert _strip_reason(batches.northstar_batch(profile_id, segments)) == _strip_reason(
            canonical.northstar_batch(profile_id, segments)
        )
    for fixture in ("ambiguous", "conditional_applicability", "contradictory",
                    "external_dependency", "injected_instruction", "mixed_modals", "unsupported"):
        segments = _segments(SPEC_SOURCE / f"{fixture}.md", "run_drift", SourceKind.SPEC)
        assert _strip_reason(batches.adversarial_batch(fixture, segments)) == _strip_reason(
            canonical.adversarial_batch(fixture, segments)
        )


def test_scenarios_cover_terminal_and_refusal_classifications() -> None:
    terminals = {
        json.loads((EXPECTED_DIR / f"{s.id}.json").read_text(encoding="utf-8"))["expected_terminal"]
        for s in SCENARIOS
    }
    assert terminals == {"DELIVERY_READY", "BLOCKED", "REFUSED_SAFE"}
