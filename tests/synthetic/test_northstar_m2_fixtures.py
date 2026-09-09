"""M2 Northstar source-profile and adversarial-fixture tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aircheck.domain.catalog import allowed_constraint_operators, get_measurement
from aircheck.domain.source import normalize_source
from aircheck.domain.types import (
    ApplicabilityStatus,
    ConstraintOperator,
    NormalizationStatus,
    Severity,
    TerminalOutcome,
)


ROOT = Path(__file__).parents[2]
SOURCE_ROOT = ROOT / "specifications" / "northstar" / "source"
PROFILE_ROOT = ROOT / "specifications" / "northstar" / "profiles"
EVAL_SPEC_ROOT = ROOT / "evals" / "specs"
EXPECTED_ROOT = ROOT / "evals" / "expected"


def test_valid_product_profiles_are_distinct_and_have_hashed_source_documents() -> None:
    """Collapsing profiles or changing a source document unnoticed must fail this test."""

    profile_data = json.loads(
        (PROFILE_ROOT / "m2_product_profiles.json").read_text(encoding="utf-8")
    )
    profiles = {item["profile_id"]: item for item in profile_data["profiles"]}
    broadcast = profiles["northstar_broadcast_master_v1"]
    preview = profiles["northstar_digital_preview_v1"]

    assert broadcast["profile_name"] == "Northstar Broadcast Master"
    assert preview["profile_name"] == "Northstar Digital Preview"
    assert broadcast["valid_product_profile"] is True
    assert preview["valid_product_profile"] is True
    for profile in (broadcast, preview):
        source = SOURCE_ROOT / profile["source_file"]
        raw = source.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == profile["source_sha256"]
        assert hashlib.sha256(normalize_source(raw).encode("utf-8")).hexdigest() == profile[
            "normalized_source_sha256"
        ]

    assert broadcast["requirements"]["captions.format"] == {"operator": "EQUALS", "value": "webvtt"}
    assert preview["requirements"]["captions.format"] == {
        "operator": "ONE_OF",
        "values": ["srt", "webvtt"],
    }
    assert "package.manifest_present" in broadcast["requirements"]
    assert "package.manifest_present" not in preview["requirements"]
    assert broadcast["requirements"]["audio.integrated_loudness"] != preview["requirements"]["audio.integrated_loudness"]
    assert broadcast["requirements"]["package.filename[role]"] != preview["requirements"]["package.filename[role]"]


def test_product_profile_predicates_use_only_closed_catalog_shapes() -> None:
    """An unknown key, unit, or operator must reject the profile fixture."""

    profile_data = json.loads(
        (PROFILE_ROOT / "m2_product_profiles.json").read_text(encoding="utf-8")
    )
    for profile in profile_data["profiles"]:
        for measurement_key, expected in profile["requirements"].items():
            definition = get_measurement(measurement_key)
            operator = ConstraintOperator(expected["operator"])
            assert operator in allowed_constraint_operators(measurement_key)
            assert expected.get("unit") == definition.canonical_unit
            if operator is ConstraintOperator.RANGE:
                assert expected["lower"] <= expected["upper"]
            elif operator is ConstraintOperator.ONE_OF:
                assert expected["values"]
            elif operator in {ConstraintOperator.PRESENT, ConstraintOperator.ABSENT}:
                assert expected["value"] is (operator is ConstraintOperator.PRESENT)
            else:
                assert "value" in expected


def test_adversarial_specs_have_hand_authored_typed_outcomes_before_m3() -> None:
    """Missing expected truth or a noncanonical outcome must fail this test."""

    expected_names = {
        "ambiguous",
        "contradictory",
        "unsupported",
        "external_dependency",
        "injected_instruction",
        "conditional_applicability",
        "mixed_modals",
    }
    assert {
        path.stem for path in EVAL_SPEC_ROOT.glob("*.md") if path.name != "README.md"
    } == expected_names
    assert {path.stem for path in EXPECTED_ROOT.glob("*.json")} == expected_names

    for name in expected_names:
        expected = json.loads((EXPECTED_ROOT / f"{name}.json").read_text(encoding="utf-8"))
        assert expected["fixture_id"] == name
        assert expected["hand_authored"] is True
        assert expected["expected_normalization_status"] in {
            item.value for item in NormalizationStatus
        }
        assert expected["expected_applicability_status"] in {
            item.value for item in ApplicabilityStatus
        }
        assert expected["expected_severity"] in {item.value for item in Severity}
        assert expected["expected_terminal_outcome"] in {
            item.value for item in TerminalOutcome
        }
        assert expected["reason_codes"]
        assert (EVAL_SPEC_ROOT / f"{name}.md").read_text(encoding="utf-8").strip()
