"""M1 deterministic constraint evaluation contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aircheck.domain.constraints import (
    AbsentConstraint,
    EqualsConstraint,
    MaxConstraint,
    MinConstraint,
    OneOfConstraint,
    PatternConstraint,
    PresentConstraint,
    RangeConstraint,
    ConstraintDraft,
    normalize_constraint,
)
from aircheck.domain.catalog import get_measurement
from aircheck.domain.terminal import Measurement, evaluate_predicate
from aircheck.domain.types import MeasurementStatus, NormalizationStatus, PredicateOutcome


def _measurement(
    key: str,
    value: str | int | float | bool | None,
    unit: str | None,
    *,
    status: MeasurementStatus = MeasurementStatus.OK,
) -> Measurement:
    return Measurement(
        measurement_id="measurement_m1_001",
        run_id="run_m1_truth_001",
        cycle=0,
        requirement_id="req_m1_001",
        measurement_key=key,
        value=value,
        unit=unit,
        status=status,
    )


@pytest.mark.parametrize(
    ("measurement", "constraint", "expected"),
    [
        (
            _measurement("container.format", "quicktime mov", None),
            EqualsConstraint(
                measurement_key="container.format", value="quicktime mov"
            ),
            PredicateOutcome.PASS,
        ),
        (
            _measurement("audio.channel_count", 2, None),
            MinConstraint(measurement_key="audio.channel_count", value=2),
            PredicateOutcome.PASS,
        ),
        (
            _measurement("audio.true_peak", -2.0, "dBTP"),
            MaxConstraint(measurement_key="audio.true_peak", unit="dBTP", value=-2.0),
            PredicateOutcome.PASS,
        ),
        (
            _measurement("audio.integrated_loudness", -26.0, "LUFS"),
            RangeConstraint(
                measurement_key="audio.integrated_loudness",
                unit="LUFS",
                lower=-26.0,
                upper=-22.0,
            ),
            PredicateOutcome.PASS,
        ),
        (
            _measurement("audio.integrated_loudness", -22.0, "LUFS"),
            RangeConstraint(
                measurement_key="audio.integrated_loudness",
                unit="LUFS",
                lower=-26.0,
                upper=-22.0,
            ),
            PredicateOutcome.PASS,
        ),
        (
            _measurement("captions.format", "webvtt", None),
            OneOfConstraint(
                measurement_key="captions.format", values=("srt", "webvtt")
            ),
            PredicateOutcome.PASS,
        ),
        (
            _measurement("package.filename[role]", "MASTER_v1.mov", None),
            PatternConstraint(
                measurement_key="package.filename[role]",
                pattern=r"MASTER_v[0-9]+\.mov",
                description="master version filename",
            ),
            PredicateOutcome.PASS,
        ),
        (
            _measurement("package.manifest_present", True, None),
            PresentConstraint(measurement_key="package.manifest_present"),
            PredicateOutcome.PASS,
        ),
        (
            _measurement("captions.present", False, None),
            AbsentConstraint(measurement_key="captions.present"),
            PredicateOutcome.PASS,
        ),
    ],
)
def test_every_frozen_operator_evaluates_typed_measurements_deterministically(
    measurement: Measurement,
    constraint: object,
    expected: PredicateOutcome,
) -> None:
    first = evaluate_predicate(measurement, constraint)
    second = evaluate_predicate(measurement, constraint)

    assert first.result is expected
    assert second == first


def test_out_of_range_typed_fact_fails_at_the_boundary() -> None:
    result = evaluate_predicate(
        _measurement("audio.integrated_loudness", -26.1, "LUFS"),
        RangeConstraint(
            measurement_key="audio.integrated_loudness",
            unit="LUFS",
            lower=-26.0,
            upper=-22.0,
        ),
    )

    assert result.result is PredicateOutcome.FAIL


def test_error_measurement_and_incompatible_unit_are_never_passes() -> None:
    constraint = RangeConstraint(
        measurement_key="audio.integrated_loudness",
        unit="LUFS",
        lower=-26.0,
        upper=-22.0,
    )

    error = evaluate_predicate(
        _measurement(
            "audio.integrated_loudness",
            None,
            "LUFS",
            status=MeasurementStatus.ERROR,
        ),
        constraint,
    )
    wrong_key = evaluate_predicate(
        _measurement("audio.true_peak", -2.0, "dBTP"),
        constraint,
    )

    assert error.result is PredicateOutcome.NOT_EVALUATED
    assert wrong_key.result is PredicateOutcome.NOT_EVALUATED
    with pytest.raises(ValidationError, match="canonical unit"):
        _measurement("audio.integrated_loudness", -24.0, "dBTP")


def test_measurements_reject_noncanonical_catalog_values() -> None:
    with pytest.raises(ValidationError, match="canonical"):
        _measurement("container.format", "QuickTime MOV", None)
    with pytest.raises(ValidationError, match="canonical unit"):
        _measurement("audio.sample_rate", 48000, "kHz")


def test_catalog_incompatible_operator_cannot_award_a_pass() -> None:
    result = evaluate_predicate(
        _measurement("container.format", "quicktime mov", None),
        MinConstraint(measurement_key="container.format", value="a"),
    )

    assert result.result is PredicateOutcome.NOT_EVALUATED


def test_admission_rejects_catalog_incompatible_operator_before_evaluation() -> None:
    constraint, status, reasons = normalize_constraint(
        ConstraintDraft(operator="MIN", raw_values=("a",), raw_unit=None),
        get_measurement("container.format"),
    )

    assert constraint is None
    assert status is NormalizationStatus.UNSUPPORTED
    assert reasons == ("OPERATOR_NOT_SUPPORTED_FOR_MEASUREMENT",)
