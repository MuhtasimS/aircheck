"""Deterministic fixture and acceptance logic for the M3a experiment."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import Field, ValidationError

from aircheck.domain.admission import CandidateRequirement, admit
from aircheck.domain.primitives import FrozenModel
from aircheck.domain.source import (
    SourceDocument,
    SourceSegment,
    ingest_source_document,
    segment_source_document,
)
from aircheck.domain.types import NormalizationStatus, ProposedObligation, SourceKind


FIXTURE_PATH = Path(__file__).with_name("fixture.md")
EXPECTED_SEGMENTS = (
    (
        "doc_m3a_fixture:s0001",
        "The program master container format must be QuickTime MOV.",
    ),
    (
        "doc_m3a_fixture:s0002",
        "The program master video frame rate must be 23.976 fps.",
    ),
    (
        "doc_m3a_fixture:s0003",
        "The program master audio sample rate shall be 48 kHz.",
    ),
    (
        "doc_m3a_fixture:s0004",
        "The program master integrated audio loudness must be between -26 and -22 LUFS.",
    ),
    (
        "doc_m3a_fixture:s0005",
        "The captions sidecar format must be WebVTT.",
    ),
    (
        "doc_m3a_fixture:s0006",
        "A SHA-256 checksum manifest should be present.",
    ),
)
EXPECTED_REQUIREMENTS = {
    "doc_m3a_fixture:s0001": ("container.format", ProposedObligation.MUST),
    "doc_m3a_fixture:s0002": ("video.frame_rate", ProposedObligation.MUST),
    "doc_m3a_fixture:s0003": ("audio.sample_rate", ProposedObligation.MUST),
    "doc_m3a_fixture:s0004": (
        "audio.integrated_loudness",
        ProposedObligation.MUST,
    ),
    "doc_m3a_fixture:s0005": ("captions.format", ProposedObligation.MUST),
    "doc_m3a_fixture:s0006": (
        "package.manifest_present",
        ProposedObligation.SHOULD,
    ),
}


class CandidateRequirementBatch(FrozenModel):
    requirements: tuple[CandidateRequirement, ...] = Field(min_length=6, max_length=6)


class UsefulnessAssessment(FrozenModel):
    useful: bool
    errors: tuple[str, ...]


class RetryOutcome(FrozenModel):
    attempts: int
    validation_failures: tuple[str, ...]


class RetryExhaustedError(RuntimeError):
    pass


def load_fixture() -> tuple[SourceDocument, tuple[SourceSegment, ...]]:
    raw = FIXTURE_PATH.read_bytes()
    document = ingest_source_document(
        doc_id="doc_m3a_fixture",
        run_id="run_m3a_provider_spike",
        kind=SourceKind.SPEC,
        title="M3a Fixed Provider Fixture",
        raw=raw,
        ingested_at=datetime(2026, 9, 6, 20, 0, tzinfo=UTC),
    )
    return document, segment_source_document(document)


def build_extraction_prompt() -> str:
    _, segments = load_fixture()
    rendered_segments = "\n".join(
        f"- {segment.segment_id}: {segment.text}" for segment in segments
    )
    return f"""Extract exactly 6 CandidateRequirement objects, one for each source segment.

This is candidate extraction only. Do not admit or verify requirements and do not call tools.
Use each segment exactly once, cite only that segment, copy its full text as quote, and assign
candidate IDs candidate_001 through candidate_006 in segment order.

Closed measurement keys for this fixture:
- container.format
- video.frame_rate
- audio.sample_rate
- audio.integrated_loudness
- captions.format
- package.manifest_present

Use only EQUALS, RANGE, or PRESENT for constraint_draft.operator. Put source values in
constraint_draft.raw_values as strings and preserve an explicit unit in raw_unit; use null when
the clause has no unit. PRESENT must use raw_values [] and raw_unit null. Asset roles are
PROGRAM_MASTER, CAPTIONS, and MANIFEST. Use stream
"video" for frame rate and "primary_audio" for audio clauses; otherwise use null. There are no
applicability conditions or referenced documents. Use EXECUTABLE only when the candidate is
complete. MUST/shall map to MUST and should maps to SHOULD. proposed_disposition is null.

Source segments:
{rendered_segments}
"""


def build_tool_prompt() -> str:
    document, _ = load_fixture()
    return f"""The fixed six-clause fixture under test is included only as lookup context:

{document.text}

Call lookup_measurement_definition exactly once with measurement_key
"audio.integrated_loudness". After the tool result, reply exactly "lookup complete".
Do not call any other tool and do not extract candidates in this round trip.
"""


def evaluate_batch(batch: CandidateRequirementBatch) -> UsefulnessAssessment:
    document, segments = load_fixture()
    errors: list[str] = []
    candidate_ids = tuple(item.candidate_id for item in batch.requirements)
    if len(candidate_ids) != len(set(candidate_ids)):
        errors.append("candidate IDs must be unique")

    seen_segments: list[str] = []
    for candidate in batch.requirements:
        if len(candidate.segment_ids) != 1:
            errors.append(
                f"{candidate.candidate_id}: expected exactly one cited fixture segment"
            )
            continue
        segment_id = candidate.segment_ids[0]
        seen_segments.append(segment_id)
        expected = EXPECTED_REQUIREMENTS.get(segment_id)
        if expected is None:
            errors.append(f"{segment_id}: unexpected fixture segment")
            continue
        expected_key, expected_obligation = expected
        if candidate.measurement_key != expected_key:
            errors.append(
                f"{segment_id}: expected measurement {expected_key}, "
                f"got {candidate.measurement_key}"
            )
        if candidate.proposed_obligation is not expected_obligation:
            errors.append(
                f"{segment_id}: expected obligation {expected_obligation.value}, "
                f"got {candidate.proposed_obligation.value}"
            )
        try:
            requirement = admit(candidate, document, segments)
        except (TypeError, ValueError) as exc:
            errors.append(f"{segment_id}: deterministic admission rejected: {exc}")
        else:
            if requirement.normalization_status is not NormalizationStatus.EXECUTABLE:
                errors.append(
                    f"{segment_id}: deterministic admission status "
                    f"{requirement.normalization_status.value}"
                )

    for segment_id in EXPECTED_REQUIREMENTS:
        count = seen_segments.count(segment_id)
        if count != 1:
            errors.append(f"{segment_id}: expected once, observed {count}")

    return UsefulnessAssessment(useful=not errors, errors=tuple(errors))


def validate_with_retry(
    operation: Callable[[int], Any],
    *,
    max_attempts: int = 2,
) -> tuple[CandidateRequirementBatch, RetryOutcome]:
    if not 1 <= max_attempts <= 2:
        raise ValueError("M3a permits at most 2 attempts")

    failures: list[str] = []
    for attempt in range(1, max_attempts + 1):
        value = operation(attempt)
        try:
            if isinstance(value, CandidateRequirementBatch):
                batch = value
            elif isinstance(value, str):
                batch = CandidateRequirementBatch.model_validate_json(value)
            else:
                batch = CandidateRequirementBatch.model_validate(value)
        except (TypeError, ValueError, ValidationError) as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
            continue
        return batch, RetryOutcome(
            attempts=attempt,
            validation_failures=tuple(failures),
        )

    raise RetryExhaustedError(
        f"candidate output remained malformed after {max_attempts} attempts"
    )
