"""Deterministic contracts for the bounded M3a provider spike."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from aircheck.domain.admission import CandidateRequirement, ScopeDraft
from aircheck.domain.constraints import ConstraintDraft
from aircheck.domain.types import NormalizationStatus, ProposedObligation
from spikes.m3a.core import EXPECTED_SEGMENTS, load_fixture


def test_fixed_fixture_has_exactly_six_stable_segments() -> None:
    """A fixture edit must not silently change the provider acceptance target."""

    _, segments = load_fixture()

    assert tuple(
        (segment.segment_id, segment.text)
        for segment in segments
    ) == EXPECTED_SEGMENTS


def _candidate(
    index: int,
    *,
    measurement_key: str,
    obligation: ProposedObligation,
    operator: str,
    values: tuple[str, ...],
    unit: str | None,
    asset_role: str,
    stream: str | None = None,
) -> CandidateRequirement:
    segment_id, quote = EXPECTED_SEGMENTS[index - 1]
    return CandidateRequirement(
        candidate_id=f"candidate_{index:03d}",
        segment_ids=(segment_id,),
        quote=quote,
        proposed_obligation=obligation,
        measurement_key=measurement_key,
        constraint_draft=ConstraintDraft(
            operator=operator,
            raw_values=values,
            raw_unit=unit,
        ),
        scope_draft=ScopeDraft(asset_role=asset_role, stream=stream),
        proposed_status=NormalizationStatus.EXECUTABLE,
        reason="Direct typed extraction from the cited clause.",
        confidence=0.95,
    )


def _valid_requirements() -> tuple[CandidateRequirement, ...]:
    return (
        _candidate(
            1,
            measurement_key="container.format",
            obligation=ProposedObligation.MUST,
            operator="EQUALS",
            values=("QuickTime MOV",),
            unit=None,
            asset_role="PROGRAM_MASTER",
        ),
        _candidate(
            2,
            measurement_key="video.frame_rate",
            obligation=ProposedObligation.MUST,
            operator="EQUALS",
            values=("23.976",),
            unit="fps",
            asset_role="PROGRAM_MASTER",
            stream="video",
        ),
        _candidate(
            3,
            measurement_key="audio.sample_rate",
            obligation=ProposedObligation.MUST,
            operator="EQUALS",
            values=("48",),
            unit="kHz",
            asset_role="PROGRAM_MASTER",
            stream="primary_audio",
        ),
        _candidate(
            4,
            measurement_key="audio.integrated_loudness",
            obligation=ProposedObligation.MUST,
            operator="RANGE",
            values=("-26", "-22"),
            unit="LUFS",
            asset_role="PROGRAM_MASTER",
            stream="primary_audio",
        ),
        _candidate(
            5,
            measurement_key="captions.format",
            obligation=ProposedObligation.MUST,
            operator="EQUALS",
            values=("WebVTT",),
            unit=None,
            asset_role="CAPTIONS",
        ),
        _candidate(
            6,
            measurement_key="package.manifest_present",
            obligation=ProposedObligation.SHOULD,
            operator="PRESENT",
            values=(),
            unit=None,
            asset_role="MANIFEST",
        ),
    )


def test_usefulness_requires_complete_executable_segment_mapping() -> None:
    """A schema-valid but semantically mis-mapped candidate must fail the bar."""

    from spikes.m3a.core import CandidateRequirementBatch, evaluate_batch

    valid = CandidateRequirementBatch(requirements=_valid_requirements())
    assert evaluate_batch(valid).model_dump() == {"useful": True, "errors": ()}

    wrong_first = valid.requirements[0].model_copy(
        update={"measurement_key": "video.codec"}
    )
    invalid = CandidateRequirementBatch(
        requirements=(wrong_first, *valid.requirements[1:])
    )

    assessment = evaluate_batch(invalid)
    assert assessment.useful is False
    assert assessment.errors == (
        "doc_m3a_fixture:s0001: expected measurement container.format, got video.codec",
    )


def test_malformed_batch_gets_one_bounded_retry_then_succeeds() -> None:
    """A malformed first response must not prevent one valid recovery attempt."""

    from spikes.m3a.core import CandidateRequirementBatch, validate_with_retry

    valid = CandidateRequirementBatch(requirements=_valid_requirements())
    attempts: list[int] = []

    def operation(attempt: int):
        attempts.append(attempt)
        return {"requirements": []} if attempt == 1 else valid

    batch, outcome = validate_with_retry(operation, max_attempts=2)

    assert batch == valid
    assert attempts == [1, 2]
    assert outcome.attempts == 2
    assert len(outcome.validation_failures) == 1


def test_retry_is_exhausted_after_two_malformed_attempts() -> None:
    """Changing the loop to make a silent third provider call must fail this test."""

    from spikes.m3a.core import RetryExhaustedError, validate_with_retry

    attempts: list[int] = []

    def operation(attempt: int):
        attempts.append(attempt)
        return {"requirements": []}

    with pytest.raises(RetryExhaustedError, match="2 attempts"):
        validate_with_retry(operation, max_attempts=2)

    assert attempts == [1, 2]


def test_retry_rejects_more_than_one_retry() -> None:
    """A caller must not expand the provider spike into an unbounded retry loop."""

    from spikes.m3a.core import validate_with_retry

    with pytest.raises(ValueError, match="at most 2 attempts"):
        validate_with_retry(lambda _: {"requirements": []}, max_attempts=3)


def test_extraction_prompt_carries_the_whole_fixed_fixture_and_closed_vocabulary() -> None:
    """Dropping a clause or catalog boundary would invalidate repeated-run evidence."""

    from spikes.m3a.core import build_extraction_prompt

    prompt = build_extraction_prompt()

    for segment_id, text in EXPECTED_SEGMENTS:
        assert segment_id in prompt
        assert text in prompt
    assert "exactly 6 CandidateRequirement objects" in prompt
    assert "package.manifest_present" in prompt
    assert "audio.integrated_loudness" in prompt
    assert "Do not admit or verify requirements" in prompt
    assert "PRESENT must use raw_values [] and raw_unit null" in prompt


def test_provider_runner_exposes_only_the_bounded_spike_controls() -> None:
    """A runner that omits provider bounds or grows product controls must fail."""

    script = Path(__file__).parents[2] / "spikes" / "m3a" / "run_spike.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--provider {bedrock,gemini}" in result.stdout
    assert "--runs RUNS" in result.stdout
    assert "--model-id MODEL_ID" in result.stdout
    assert "--output OUTPUT" in result.stdout
    assert "remediate" not in result.stdout.casefold()
    assert "deploy" not in result.stdout.casefold()


def test_receipt_serialization_omits_opaque_provider_reasoning_signatures() -> None:
    """Provider-internal signatures must not bloat or contaminate evidence receipts."""

    from spikes.m3a.run_spike import _json_safe

    assert _json_safe(
        {
            "reasoningSignature": "opaque-provider-value",
            "toolUseId": "tooluse_001",
            "input": {"measurement_key": "audio.integrated_loudness"},
        }
    ) == {
        "toolUseId": "tooluse_001",
        "input": {"measurement_key": "audio.integrated_loudness"},
    }


def test_tool_prompt_keeps_fixture_but_removes_conflicting_extraction_command() -> None:
    """The tool round trip must not accidentally request a second extraction task."""

    from spikes.m3a.core import build_tool_prompt

    prompt = build_tool_prompt()

    for _, text in EXPECTED_SEGMENTS:
        assert text in prompt
    assert "Call lookup_measurement_definition exactly once" in prompt
    assert "Extract exactly 6" not in prompt


def test_tool_metric_receipt_keeps_counts_and_input_without_provider_payload() -> None:
    """Raw SDK tool payloads must collapse to the evidence needed by M3a."""

    from spikes.m3a.run_spike import _tool_metric_summary

    assert _tool_metric_summary(
        {
            "lookup": {
                "call_count": 1,
                "error_count": 0,
                "success_count": 1,
                "total_time": 0.25,
                "tool": {
                    "input": {"measurement_key": "audio.integrated_loudness"},
                    "reasoningSignature": "opaque-provider-value",
                    "toolUseId": "tooluse_001",
                },
            }
        }
    ) == {
        "lookup": {
            "call_count": 1,
            "error_count": 0,
            "success_count": 1,
            "total_time": 0.25,
            "input": {"measurement_key": "audio.integrated_loudness"},
        }
    }


def test_freeform_receipt_text_removes_model_thinking_tags() -> None:
    """A provider's visible reasoning wrapper must not enter durable evidence."""

    from spikes.m3a.run_spike import _receipt_text

    assert _receipt_text(
        "<thinking>internal procedural text</thinking>\n\nlookup complete"
    ) == "lookup complete"
    assert _receipt_text("private unstructured provider prose") == (
        "<omitted: non-contractual provider response>"
    )
