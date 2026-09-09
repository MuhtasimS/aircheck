"""R2.5 tests for constraints, catalog closure, and fail-closed admission."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aircheck.domain.admission import (
    CandidateRequirement,
    ConditionDraft,
    Requirement,
    ScopeDraft,
    admit,
    find_contradictions,
    mark_contradictions,
    modal_severity,
)
from aircheck.domain.catalog import measurement_catalog
from aircheck.domain.constraints import (
    AbsentConstraint,
    Constraint,
    ConstraintDraft,
    EqualsConstraint,
    MaxConstraint,
    MinConstraint,
    OneOfConstraint,
    PatternConstraint,
    PresentConstraint,
    RangeConstraint,
    render_constraint,
)
from aircheck.domain.source import ingest_source_document, segment_source_document
from aircheck.domain.types import (
    ApplicabilityStatus,
    AutomationDisposition,
    ConditionField,
    ConditionOperator,
    ConstraintOperator,
    NormalizationStatus,
    ProposedObligation,
    Severity,
    SourceKind,
)
from pydantic import TypeAdapter


def _source(text: str, *, doc_id: str = "doc_admission"):
    document = ingest_source_document(
        doc_id=doc_id,
        run_id="run_admission_001",
        kind=SourceKind.SPEC,
        title="Northstar Broadcast Master",
        raw=text.encode("utf-8"),
        ingested_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    return document, segment_source_document(document)


def _candidate(
    segments,
    *,
    measurement_key: str = "audio.integrated_loudness",
    operator: str = "RANGE",
    values: tuple[str, ...] = ("-26", "-22"),
    unit: str | None = "LKFS",
    proposed_status: NormalizationStatus = NormalizationStatus.EXECUTABLE,
    conditions: tuple[ConditionDraft, ...] = (),
    referenced_document: str | None = None,
) -> CandidateRequirement:
    return CandidateRequirement(
        candidate_id="candidate_001",
        segment_ids=(segments[0].segment_id,),
        quote=segments[0].text,
        proposed_obligation=ProposedObligation.MAY,
        measurement_key=measurement_key,
        constraint_draft=ConstraintDraft(
            operator=operator,
            raw_values=values,
            raw_unit=unit,
        ),
        scope_draft=ScopeDraft(asset_role="PROGRAM_MASTER", stream="primary_audio"),
        applicability_draft=conditions,
        proposed_status=proposed_status,
        reason="model proposal",
        confidence=0.98,
        referenced_document=referenced_document,
    )


def test_measurement_catalog_is_the_exact_closed_22_key_vocabulary() -> None:
    """An unreviewed measurement key would silently expand executable authority."""
    assert tuple(item.measurement_key for item in measurement_catalog()) == (
        "package.file_present[role]",
        "package.filename[role]",
        "package.manifest_present",
        "package.checksums_match",
        "container.format",
        "video.codec",
        "video.width",
        "video.height",
        "video.frame_rate",
        "video.scan_type",
        "audio.channel_count",
        "audio.channel_layout",
        "audio.sample_rate",
        "audio.integrated_loudness",
        "audio.true_peak",
        "audio.loudness_range",
        "captions.present",
        "captions.format",
        "captions.cue_count",
        "captions.first_cue_start",
        "captions.overlapping_cues",
        "captions.max_cue_end_vs_duration",
    )


def test_admission_owns_severity_unit_and_deterministic_identity() -> None:
    document, segments = _source("Program loudness shall be -24 LKFS +/-2 dB.")
    candidate = _candidate(segments)

    first = admit(candidate, document, segments)
    second = admit(candidate, document, segments)

    assert first.normalization_status is NormalizationStatus.EXECUTABLE
    assert first.severity is Severity.BLOCKING
    assert first.severity_source == "shall"
    assert first.requirement_id == second.requirement_id
    assert isinstance(first.constraint, RangeConstraint)
    assert first.constraint.unit == "LUFS"
    assert first.automation_disposition is AutomationDisposition.HUMAN_APPROVAL_REQUIRED
    assert "between -26 and -22 LUFS inclusive" in first.rendered_text


def test_container_format_alias_preserves_the_complete_source_value() -> None:
    document, segments = _source("The program master MUST be a QuickTime MOV file.")
    candidate = _candidate(
        segments,
        measurement_key="container.format",
        operator="EQUALS",
        values=("QuickTime",),
        unit=None,
    )

    requirement = admit(candidate, document, segments)

    assert requirement.normalization_status is NormalizationStatus.EXECUTABLE
    assert isinstance(requirement.constraint, EqualsConstraint)
    assert requirement.constraint.value == "quicktime mov"


def test_container_format_alias_does_not_expand_a_partial_source_value() -> None:
    document, segments = _source("The program master MUST be a QuickTime file.")
    candidate = _candidate(
        segments,
        measurement_key="container.format",
        operator="EQUALS",
        values=("QuickTime",),
        unit=None,
    )

    requirement = admit(candidate, document, segments)

    assert requirement.normalization_status is NormalizationStatus.EXECUTABLE
    assert isinstance(requirement.constraint, EqualsConstraint)
    assert requirement.constraint.value == "quicktime"


@pytest.mark.parametrize(
    ("text", "severity", "source"),
    [
        ("Captions are recommended.", Severity.WARNING, "recommended"),
        ("Captions are optional.", Severity.INFO, "optional"),
        ("Captions are supplied.", Severity.BLOCKING, "UNKNOWN"),
        ("Captions shall be supplied but may be omitted.", Severity.BLOCKING, "MIXED"),
    ],
)
def test_modal_severity_fails_closed(text: str, severity: Severity, source: str) -> None:
    assert modal_severity(text) == (severity, source)


def test_trusted_requirement_cannot_be_minted_directly() -> None:
    document, segments = _source("Program loudness shall be -24 LKFS +/-2 dB.")
    admitted = admit(_candidate(segments), document, segments)

    with pytest.raises(TypeError, match="admit"):
        Requirement(**admitted.model_dump())


@pytest.mark.parametrize(
    ("candidate_kwargs", "expected_status", "expected_reason"),
    [
        (
            {"measurement_key": "audio.magic"},
            NormalizationStatus.UNSUPPORTED,
            "UNKNOWN_MEASUREMENT_KEY",
        ),
        (
            {"values": ()},
            NormalizationStatus.AMBIGUOUS,
            "MISSING_RANGE_VALUES",
        ),
        (
            {"unit": "furlongs"},
            NormalizationStatus.AMBIGUOUS,
            "UNKNOWN_UNIT",
        ),
        (
            {"operator": "MAGIC"},
            NormalizationStatus.UNSUPPORTED,
            "UNKNOWN_CONSTRAINT_OPERATOR",
        ),
    ],
)
def test_incomplete_or_unknown_semantics_fail_closed(
    candidate_kwargs,
    expected_status,
    expected_reason,
) -> None:
    document, segments = _source("Program loudness shall be -24 LKFS +/-2 dB.")

    requirement = admit(_candidate(segments, **candidate_kwargs), document, segments)

    assert requirement.normalization_status is expected_status
    assert expected_reason in requirement.status_reasons
    assert requirement.constraint is None


def test_model_non_executable_status_is_never_upgraded() -> None:
    document, segments = _source("Program loudness shall be -24 LKFS +/-2 dB.")

    requirement = admit(
        _candidate(segments, proposed_status=NormalizationStatus.AMBIGUOUS),
        document,
        segments,
    )

    assert requirement.normalization_status is NormalizationStatus.AMBIGUOUS
    assert "MODEL_STATUS_CEILING" in requirement.status_reasons


def test_missing_required_audio_stream_is_not_admitted_as_executable() -> None:
    document, segments = _source("Program loudness shall be -24 LKFS +/-2 dB.")
    candidate = _candidate(segments).model_copy(
        update={"scope_draft": ScopeDraft(asset_role="PROGRAM_MASTER")}
    )

    requirement = admit(candidate, document, segments)

    assert requirement.normalization_status is NormalizationStatus.AMBIGUOUS
    assert requirement.constraint is None
    assert requirement.applicability_status is ApplicabilityStatus.UNRESOLVED
    assert "MISSING_REQUIRED_STREAM" in requirement.status_reasons


def test_source_scope_normalizes_parameterized_filename_measurement_key() -> None:
    document, segments = _source(
        "The program master filename MUST exactly equal `PROGRAM.mov`."
    )
    candidate = _candidate(
        segments,
        measurement_key="package.filename[PROGRAM_MASTER]",
        operator="EQUALS",
        values=("PROGRAM.mov",),
        unit=None,
    )

    requirement = admit(candidate, document, segments)

    assert requirement.normalization_status is NormalizationStatus.EXECUTABLE
    assert requirement.verification is not None
    assert requirement.verification.measurement_key == "package.filename[role]"
    assert requirement.constraint is not None
    assert requirement.constraint.measurement_key == "package.filename[role]"
    assert "PARAMETERIZED_MEASUREMENT_KEY_NORMALIZED" in requirement.status_reasons


def test_parameterized_measurement_key_cannot_invent_a_different_scope_role() -> None:
    document, segments = _source(
        "The program master filename MUST exactly equal `PROGRAM.mov`."
    )
    candidate = _candidate(
        segments,
        measurement_key="package.filename[CAPTIONS]",
        operator="EQUALS",
        values=("PROGRAM.mov",),
        unit=None,
    )

    requirement = admit(candidate, document, segments)

    assert requirement.normalization_status is NormalizationStatus.UNSUPPORTED
    assert requirement.constraint is None
    assert "PARAMETERIZED_MEASUREMENT_ROLE_MISMATCH" in requirement.status_reasons


def test_applicability_accepts_only_source_supported_polarity() -> None:
    document, segments = _source(
        "Program loudness shall be -24 LKFS, except promotional material may be up to -20 LKFS."
    )
    supported = ConditionDraft(
        field="run.content_type",
        op="NEQ",
        values=("promotional material",),
        anchor_segment_id=segments[0].segment_id,
    )
    reversed_condition = supported.model_copy(update={"op": "EQ"})

    admitted = admit(
        _candidate(segments, conditions=(supported,)),
        document,
        segments,
    )
    rejected = admit(
        _candidate(segments, conditions=(reversed_condition,)),
        document,
        segments,
    )

    assert admitted.applicability_status is ApplicabilityStatus.APPLICABLE
    assert admitted.applicability[0].field is ConditionField.RUN_CONTENT_TYPE
    assert admitted.applicability[0].op is ConditionOperator.NEQ
    assert rejected.applicability_status is ApplicabilityStatus.UNRESOLVED
    assert "APPLICABILITY_POLARITY_UNPROVEN" in rejected.status_reasons


def test_uncertain_conditional_language_never_becomes_global() -> None:
    document, segments = _source(
        "Program loudness shall be -24 LKFS unless the distributor requests otherwise."
    )

    requirement = admit(_candidate(segments), document, segments)

    assert requirement.applicability_status is ApplicabilityStatus.UNRESOLVED
    assert "APPLICABILITY_UNRESOLVED" in requirement.status_reasons


def test_out_of_vocabulary_applicability_is_unresolved_without_erasing_constraint() -> None:
    document, segments = _source("Program loudness shall be -24 LKFS only for episode 7.")
    unsupported = ConditionDraft(
        field="run.episode",
        op="EQ",
        values=("episode 7",),
        anchor_segment_id=segments[0].segment_id,
    )

    requirement = admit(
        _candidate(segments, conditions=(unsupported,)),
        document,
        segments,
    )

    assert requirement.normalization_status is NormalizationStatus.EXECUTABLE
    assert requirement.applicability_status is ApplicabilityStatus.UNRESOLVED
    assert requirement.constraint is not None
    assert "UNSUPPORTED_APPLICABILITY_FIELD" in requirement.status_reasons


def test_spurious_condition_on_unconditional_source_is_ignored() -> None:
    document, segments = _source("Program loudness shall be -24 LKFS +/-2 dB.")
    spurious = ConditionDraft(
        field="run.content_type",
        op="EQ",
        values=("promotional",),
        anchor_segment_id=segments[0].segment_id,
    )

    requirement = admit(
        _candidate(segments, conditions=(spurious,)),
        document,
        segments,
    )

    assert requirement.applicability == ()
    assert requirement.applicability_status is ApplicabilityStatus.APPLICABLE
    assert "SPURIOUS_APPLICABILITY_IGNORED" in requirement.status_reasons


def test_missing_referenced_document_is_external_dependency() -> None:
    document, segments = _source(
        "Captions shall conform to the distributor accessibility standard."
    )

    requirement = admit(
        _candidate(
            segments,
            measurement_key="captions.present",
            operator="PRESENT",
            values=(),
            unit=None,
            referenced_document="Distributor Accessibility Standard",
        ),
        document,
        segments,
        available_document_titles=(),
    )

    assert requirement.normalization_status is NormalizationStatus.EXTERNAL_DEPENDENCY
    assert "REFERENCED_DOCUMENT_ABSENT" in requirement.status_reasons


def test_pattern_must_compile_before_it_is_executable() -> None:
    document, segments = _source("Program masters shall match the required naming pattern.")

    requirement = admit(
        _candidate(
            segments,
            measurement_key="package.filename[role]",
            operator="MATCHES_PATTERN",
            values=("[unterminated", "PROGRAM_EP##_NBM_YYYYMMDD.mov"),
            unit=None,
        ),
        document,
        segments,
    )

    assert requirement.normalization_status is NormalizationStatus.AMBIGUOUS
    assert "INVALID_PATTERN" in requirement.status_reasons


def test_overlapping_disjoint_equalities_are_reported_as_contradictory() -> None:
    first_doc, first_segments = _source(
        "Audio sample rate must be 48 kHz.",
        doc_id="doc_rate_48",
    )
    second_doc, second_segments = _source(
        "Audio sample rate must be 96 kHz.",
        doc_id="doc_rate_96",
    )
    first = admit(
        _candidate(
            first_segments,
            measurement_key="audio.sample_rate",
            operator="EQUALS",
            values=("48",),
            unit="kHz",
        ),
        first_doc,
        first_segments,
    )
    second = admit(
        _candidate(
            second_segments,
            measurement_key="audio.sample_rate",
            operator="EQUALS",
            values=("96",),
            unit="kHz",
        ),
        second_doc,
        second_segments,
    )

    assert first.constraint.value == 48000  # type: ignore[union-attr]
    assert second.constraint.value == 96000  # type: ignore[union-attr]
    assert find_contradictions((first, second)) == (
        (first.requirement_id, second.requirement_id),
    )
    marked = mark_contradictions((first, second))
    assert all(
        requirement.normalization_status is NormalizationStatus.CONTRADICTORY
        for requirement in marked
    )
    assert second.requirement_id in marked[0].status_reasons
    assert first.requirement_id in marked[1].status_reasons


def test_all_eight_constraints_have_deterministic_renderers() -> None:
    entries = measurement_catalog()
    assert len({item.measurement_key for item in entries}) == 22
    assert tuple(operator.value for operator in ConstraintOperator) == (
        "EQUALS",
        "MIN",
        "MAX",
        "RANGE",
        "ONE_OF",
        "MATCHES_PATTERN",
        "PRESENT",
        "ABSENT",
    )
    constraint = RangeConstraint(
        measurement_key="audio.integrated_loudness",
        unit="LUFS",
        lower=-26,
        upper=-22,
        inclusive_lower=True,
        inclusive_upper=True,
    )
    assert render_constraint(constraint, label="integrated program loudness") == (
        "integrated program loudness between -26 and -22 LUFS inclusive"
    )


def test_all_eight_constraint_variants_round_trip_through_typed_json() -> None:
    constraints: tuple[Constraint, ...] = (
        EqualsConstraint(measurement_key="video.width", unit="px", value=1920),
        MinConstraint(measurement_key="captions.cue_count", value=1),
        MaxConstraint(measurement_key="audio.true_peak", unit="dBTP", value=-2),
        RangeConstraint(
            measurement_key="audio.integrated_loudness",
            unit="LUFS",
            lower=-26,
            upper=-22,
        ),
        OneOfConstraint(measurement_key="video.codec", values=("prores", "dnxhr")),
        PatternConstraint(
            measurement_key="package.filename[role]",
            pattern=r"^[A-Z]+\.mov$",
            description="PROGRAM.mov",
        ),
        PresentConstraint(measurement_key="captions.present"),
        AbsentConstraint(measurement_key="captions.present"),
    )
    adapter = TypeAdapter(Constraint)

    assert tuple(
        adapter.validate_json(adapter.dump_json(constraint)) for constraint in constraints
    ) == constraints
