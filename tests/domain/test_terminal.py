"""R2.6 terminal choke-point and predicate safety tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aircheck.domain.constraints import AbsentConstraint, PresentConstraint
from aircheck.domain.terminal import (
    DecisionFact,
    FindingFact,
    Measurement,
    PredicateResult,
    RequirementFact,
    TerminalInput,
    TerminalVerdict,
    compute,
    evaluate_predicate,
)
from aircheck.domain.types import (
    ApplicabilityStatus,
    DecisionRequestStatus,
    FindingLifecycle,
    MeasurementStatus,
    NormalizationStatus,
    PredicateOutcome,
    Severity,
    TerminalOutcome,
)


def _measurement(status: MeasurementStatus = MeasurementStatus.OK) -> Measurement:
    return Measurement(
        measurement_id="measurement_001",
        run_id="run_terminal_001",
        cycle=0,
        requirement_id="req_001",
        measurement_key="package.manifest_present",
        value=True if status is MeasurementStatus.OK else None,
        unit=None,
        status=status,
    )


def test_error_measurement_can_never_be_recorded_as_pass() -> None:
    result = evaluate_predicate(
        _measurement(MeasurementStatus.ERROR),
        PresentConstraint(measurement_key="package.manifest_present"),
    )
    assert result.result is PredicateOutcome.NOT_EVALUATED
    with pytest.raises(TypeError, match="evaluate_predicate"):
        PredicateResult(
            requirement_id="req_001",
            measurement_id="measurement_001",
            result=PredicateOutcome.PASS,
        )


def test_terminal_verdict_cannot_be_constructed_outside_compute() -> None:
    with pytest.raises(TypeError, match="compute"):
        TerminalVerdict(
            run_id="run_terminal_001",
            cycle=0,
            outcome=TerminalOutcome.DELIVERY_READY,
            counts={
                "applicable_blocking": 0,
                "passed": 0,
                "failed": 0,
                "not_evaluated": 0,
                "unresolved_spec": 0,
                "pending_decisions": 0,
                "autonomous_remediations": 0,
                "authorized_remediations": 0,
            },
            reasons=(),
            originals_integrity_verified=True,
            computed_at=datetime.now(UTC),
        )


def test_clean_terminal_input_earns_delivery_ready() -> None:
    verdict = compute(
        TerminalInput(run_id="run_terminal_001", cycle=0, originals_integrity_verified=True)
    )
    assert verdict is not None
    assert verdict.outcome is TerminalOutcome.DELIVERY_READY
    assert verdict.reasons == ()


@pytest.mark.parametrize(
    ("input_update", "reason_prefix"),
    [
        (
            {
                "requirements": (
                    RequirementFact(
                        requirement_id="req_001",
                        severity=Severity.BLOCKING,
                        normalization_status=NormalizationStatus.AMBIGUOUS,
                        applicability_status=ApplicabilityStatus.UNRESOLVED,
                    ),
                )
            },
            "SPEC_UNRESOLVED",
        ),
        ({"originals_integrity_verified": False}, "ORIGINAL_INTEGRITY_FAILED"),
        (
            {
                "requirements": (
                    RequirementFact(
                        requirement_id="req_001",
                        severity=Severity.BLOCKING,
                        normalization_status=NormalizationStatus.EXECUTABLE,
                        applicability_status=ApplicabilityStatus.APPLICABLE,
                    ),
                ),
                "predicates": (
                    evaluate_predicate(
                        _measurement(MeasurementStatus.ERROR),
                        PresentConstraint(measurement_key="package.manifest_present"),
                    ),
                ),
            },
            "REQUIREMENT_NOT_PASSED",
        ),
    ],
)
def test_unresolved_truth_blocks_when_no_action_can_help(input_update, reason_prefix: str) -> None:
    base = TerminalInput(
        run_id="run_terminal_001", cycle=0, originals_integrity_verified=True
    )
    verdict = compute(base.model_copy(update=input_update))
    assert verdict is not None and verdict.outcome is TerminalOutcome.BLOCKED
    assert any(reason.startswith(reason_prefix) for reason in verdict.reasons)


def test_open_admissible_remediation_defers_terminal_verdict() -> None:
    terminal_input = TerminalInput(
        run_id="run_terminal_001",
        cycle=0,
        requirements=(
            RequirementFact(
                requirement_id="req_001",
                severity=Severity.BLOCKING,
                normalization_status=NormalizationStatus.EXECUTABLE,
                applicability_status=ApplicabilityStatus.APPLICABLE,
            ),
        ),
        predicates=(
            evaluate_predicate(
                _measurement(),
                AbsentConstraint(measurement_key="package.manifest_present"),
            ),
        ),
        findings=(
            FindingFact(
                requirement_id="req_001",
                status=FindingLifecycle.OPEN,
                has_admissible_option=True,
            ),
        ),
        originals_integrity_verified=True,
    )
    assert compute(terminal_input) is None


def test_pending_decision_defers_terminal_verdict() -> None:
    terminal_input = TerminalInput(
        run_id="run_terminal_001",
        cycle=0,
        decisions=(DecisionFact(status=DecisionRequestStatus.PENDING),),
        originals_integrity_verified=True,
    )
    assert compute(terminal_input) is None
