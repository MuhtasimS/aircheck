"""Bounded S1 interpretation and deterministic-admission tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aircheck.agent.contracts import InventoryAsset, PackageInventory
from aircheck.domain.source import ingest_source_document, segment_source_document
from aircheck.domain.types import (
    ApplicabilityStatus,
    NormalizationStatus,
    Severity,
    SourceKind,
)


class ScriptedModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def generate(self, prompt, output_model):
        self.prompts.append((prompt, output_model))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


def _source(text: str, *, doc_id: str = "doc_s1"):
    document = ingest_source_document(
        doc_id=doc_id,
        run_id="run_m3_s1",
        kind=SourceKind.SPEC,
        title="S1 fixture",
        raw=text.encode(),
        ingested_at=datetime(2026, 9, 7, tzinfo=UTC),
    )
    return document, segment_source_document(document)


def _inventory() -> PackageInventory:
    return PackageInventory(
        run_id="run_m3_s1",
        content_type="program",
        destination_profile="Northstar Broadcast Master",
        assets=(
            InventoryAsset(
                asset_id="asset_master",
                role="PROGRAM_MASTER",
                filename="master.mov",
            ),
        ),
    )


def _candidate(
    segment_id: str,
    quote: str,
    *,
    candidate_id: str = "candidate_audio",
    obligation: str = "SHOULD",
    measurement_key: str | None = "audio.sample_rate",
    operator: str | None = "EQUALS",
    values: tuple[str, ...] = ("48",),
    unit: str | None = "kHz",
    proposed_status: str = "EXECUTABLE",
):
    return {
        "candidate_id": candidate_id,
        "segment_ids": [segment_id],
        "quote": quote,
        "proposed_obligation": obligation,
        "measurement_key": measurement_key,
        "constraint_draft": None
        if operator is None
        else {"operator": operator, "raw_values": list(values), "raw_unit": unit},
        "scope_draft": {"asset_role": "PROGRAM_MASTER", "stream": "primary_audio"},
        "applicability_draft": [],
        "proposed_status": proposed_status,
        "reason": "Typed interpretation of the cited clause.",
        "confidence": 0.9,
        "proposed_disposition": None,
        "referenced_document": None,
    }


def test_s1_retries_malformed_batch_then_admits_with_source_owned_severity() -> None:
    from aircheck.agent.interpretation import interpret_requirements

    document, segments = _source("Program audio MUST be 48 kHz.")
    valid = {
        "requirements": [
            _candidate(segments[0].segment_id, segments[0].text, obligation="SHOULD")
        ]
    }
    model = ScriptedModel([{"requirements": []}, valid])

    result = interpret_requirements(model, document, _inventory())

    assert result.attempts == 2
    assert result.validation_failures == ("SCHEMA_INVALID",)
    assert result.requirements[0].normalization_status is NormalizationStatus.EXECUTABLE
    assert result.requirements[0].severity is Severity.BLOCKING
    assert result.requirements[0].severity_source == "must"
    assert "SCHEMA_INVALID" in model.prompts[1][0]


def test_s1_retries_provenance_rejection_without_accepting_model_offsets() -> None:
    from aircheck.agent.interpretation import interpret_requirements

    document, segments = _source("Program audio MUST be 48 kHz.")
    invalid = {
        "requirements": [
            _candidate("doc_s1:s9999", segments[0].text)
        ]
    }
    valid = {
        "requirements": [
            _candidate(segments[0].segment_id, segments[0].text)
        ]
    }

    result = interpret_requirements(
        ScriptedModel([invalid, valid]), document, _inventory()
    )

    assert result.attempts == 2
    assert result.validation_failures == ("PROVENANCE_INVALID",)
    assert result.requirements[0].span.start == segments[0].start
    assert result.requirements[0].span.end == segments[0].end


def test_schema_valid_non_executable_candidate_remains_explicit_and_unresolved() -> None:
    from aircheck.agent.interpretation import interpret_requirements

    document, segments = _source("Program audio MUST meet broadcast-quality loudness.")
    output = {
        "requirements": [
            _candidate(
                segments[0].segment_id,
                segments[0].text,
                measurement_key=None,
                operator=None,
                proposed_status="AMBIGUOUS",
            )
        ]
    }

    result = interpret_requirements(ScriptedModel([output]), document, _inventory())
    requirement = result.requirements[0]

    assert requirement.normalization_status is NormalizationStatus.AMBIGUOUS
    assert requirement.applicability_status is ApplicabilityStatus.UNRESOLVED
    assert requirement.constraint is None
    assert "MISSING_MEASUREMENT_KEY" in requirement.status_reasons


def test_s1_marks_overlapping_disjoint_equalities_contradictory() -> None:
    from aircheck.agent.interpretation import interpret_requirements

    document, segments = _source(
        "Program audio MUST measure -24 LUFS. Program audio MUST measure -16 LUFS."
    )
    output = {
        "requirements": [
            _candidate(
                segments[0].segment_id,
                segments[0].text,
                candidate_id="candidate_loudness_24",
                measurement_key="audio.integrated_loudness",
                values=("-24",),
                unit="LUFS",
            ),
            _candidate(
                segments[1].segment_id,
                segments[1].text,
                candidate_id="candidate_loudness_16",
                measurement_key="audio.integrated_loudness",
                values=("-16",),
                unit="LUFS",
            ),
        ]
    }

    result = interpret_requirements(ScriptedModel([output]), document, _inventory())

    assert len(result.requirements) == 2
    assert all(
        requirement.normalization_status is NormalizationStatus.CONTRADICTORY
        for requirement in result.requirements
    )
    assert all(
        requirement.applicability_status is ApplicabilityStatus.APPLICABLE
        for requirement in result.requirements
    )


def test_s1_stops_after_one_retry_and_reports_typed_exhaustion() -> None:
    from aircheck.agent.interpretation import InterpretationExhaustedError, interpret_requirements

    document, _ = _source("Program audio MUST be 48 kHz.")
    model = ScriptedModel([RuntimeError("opaque one"), RuntimeError("opaque two")])

    with pytest.raises(InterpretationExhaustedError, match="2 attempts") as captured:
        interpret_requirements(model, document, _inventory())

    assert captured.value.failures == ("PROVIDER_ERROR:RuntimeError",) * 2
    assert len(model.prompts) == 2
