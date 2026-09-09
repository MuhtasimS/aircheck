"""Sole terminal-verdict and predicate-construction choke points."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from pydantic import Field, ValidationInfo, model_validator

from aircheck.domain.catalog import (
    allowed_constraint_operators,
    validate_measurement_value,
)
from aircheck.domain.constraints import (
    AbsentConstraint,
    Constraint,
    EqualsConstraint,
    MaxConstraint,
    MinConstraint,
    OneOfConstraint,
    PatternConstraint,
    PresentConstraint,
    RangeConstraint,
)
from aircheck.domain.primitives import (
    CanonicalScalar,
    FrozenModel,
    MeasurementId,
    NonEmptyStr,
    RequirementId,
    RunId,
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


_PREDICATE_CONTEXT_KEY = "aircheck_predicate_factory"
_PREDICATE_CONTEXT_TOKEN = object()
_TERMINAL_CONTEXT_KEY = "aircheck_terminal_factory"
_TERMINAL_CONTEXT_TOKEN = object()


class Measurement(FrozenModel):
    measurement_id: MeasurementId
    run_id: RunId
    cycle: int = Field(ge=0)
    requirement_id: RequirementId
    measurement_key: NonEmptyStr
    value: CanonicalScalar | None = None
    unit: str | None = None
    status: MeasurementStatus
    error: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_fact_shape(self) -> Measurement:
        if self.status is MeasurementStatus.ERROR:
            if self.value is not None:
                raise ValueError("ERROR measurements cannot carry a value")
            return self
        if self.value is None:
            raise ValueError("OK measurements require a canonical value")
        if self.error is not None:
            raise ValueError("OK measurements cannot carry an error")
        validate_measurement_value(self.measurement_key, self.value, self.unit)
        return self


class PredicateResult(FrozenModel):
    requirement_id: RequirementId
    measurement_id: MeasurementId
    result: PredicateOutcome

    @model_validator(mode="before")
    @classmethod
    def require_evaluator(cls, value: Any, info: ValidationInfo) -> Any:
        context = info.context or {}
        if context.get(_PREDICATE_CONTEXT_KEY) is not _PREDICATE_CONTEXT_TOKEN:
            raise TypeError("PredicateResult must be created by evaluate_predicate()")
        return value


class RequirementFact(FrozenModel):
    requirement_id: RequirementId
    severity: Severity
    normalization_status: NormalizationStatus
    applicability_status: ApplicabilityStatus


class FindingFact(FrozenModel):
    requirement_id: RequirementId
    status: FindingLifecycle
    has_admissible_option: bool


class DecisionFact(FrozenModel):
    status: DecisionRequestStatus


class TerminalInput(FrozenModel):
    run_id: RunId
    cycle: int = Field(ge=0)
    requirements: tuple[RequirementFact, ...] = ()
    predicates: tuple[PredicateResult, ...] = ()
    findings: tuple[FindingFact, ...] = ()
    decisions: tuple[DecisionFact, ...] = ()
    originals_integrity_verified: bool
    autonomous_remediations: int = Field(default=0, ge=0)
    authorized_remediations: int = Field(default=0, ge=0)


class TerminalCounts(FrozenModel):
    applicable_blocking: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    not_evaluated: int = Field(ge=0)
    unresolved_spec: int = Field(ge=0)
    pending_decisions: int = Field(ge=0)
    autonomous_remediations: int = Field(ge=0)
    authorized_remediations: int = Field(ge=0)


class TerminalVerdict(FrozenModel):
    run_id: RunId
    cycle: int = Field(ge=0)
    outcome: TerminalOutcome
    counts: TerminalCounts
    reasons: tuple[str, ...]
    originals_integrity_verified: bool
    computed_at: datetime

    @model_validator(mode="before")
    @classmethod
    def require_compute(cls, value: Any, info: ValidationInfo) -> Any:
        context = info.context or {}
        if context.get(_TERMINAL_CONTEXT_KEY) is not _TERMINAL_CONTEXT_TOKEN:
            raise TypeError("TerminalVerdict must be created by terminal.compute()")
        return value


def evaluate_predicate(
    measurement: Measurement,
    constraint: Constraint,
) -> PredicateResult:
    """Evaluate one typed constraint over one deterministic measurement fact."""

    result = PredicateOutcome.NOT_EVALUATED
    if (
        measurement.status is MeasurementStatus.OK
        and measurement.measurement_key == constraint.measurement_key
        and measurement.unit == constraint.unit
        and measurement.value is not None
        and constraint.operator
        in allowed_constraint_operators(measurement.measurement_key)
    ):
        try:
            value = measurement.value
            if isinstance(constraint, EqualsConstraint):
                matched = value == constraint.value
            elif isinstance(constraint, MinConstraint):
                matched = (
                    value >= constraint.value
                    if constraint.inclusive
                    else value > constraint.value
                )
            elif isinstance(constraint, MaxConstraint):
                matched = (
                    value <= constraint.value
                    if constraint.inclusive
                    else value < constraint.value
                )
            elif isinstance(constraint, RangeConstraint):
                lower = (
                    value >= constraint.lower
                    if constraint.inclusive_lower
                    else value > constraint.lower
                )
                upper = (
                    value <= constraint.upper
                    if constraint.inclusive_upper
                    else value < constraint.upper
                )
                matched = lower and upper
            elif isinstance(constraint, OneOfConstraint):
                matched = value in constraint.values
            elif isinstance(constraint, PatternConstraint):
                matched = type(value) is str and re.fullmatch(
                    constraint.pattern, value
                ) is not None
            elif isinstance(constraint, PresentConstraint):
                matched = value is True
            elif isinstance(constraint, AbsentConstraint):
                matched = value is False
            else:  # pragma: no cover - Constraint is a closed discriminated union.
                matched = False
        except TypeError:
            matched = None
        if matched is not None:
            result = PredicateOutcome.PASS if matched else PredicateOutcome.FAIL
    return PredicateResult.model_validate(
        {
            "requirement_id": measurement.requirement_id,
            "measurement_id": measurement.measurement_id,
            "result": result,
        },
        context={_PREDICATE_CONTEXT_KEY: _PREDICATE_CONTEXT_TOKEN},
    )


def compute(terminal_input: TerminalInput) -> TerminalVerdict | None:
    """Return an earned terminal verdict or None while admissible work remains."""

    blocking = tuple(
        requirement
        for requirement in terminal_input.requirements
        if requirement.severity is Severity.BLOCKING
    )
    unresolved = tuple(
        requirement
        for requirement in blocking
        if requirement.normalization_status is not NormalizationStatus.EXECUTABLE
        or requirement.applicability_status is ApplicabilityStatus.UNRESOLVED
    )
    applicable = tuple(
        requirement
        for requirement in blocking
        if requirement.normalization_status is NormalizationStatus.EXECUTABLE
        and requirement.applicability_status is ApplicabilityStatus.APPLICABLE
    )
    latest = {
        requirement.requirement_id: next(
            (
                predicate
                for predicate in reversed(terminal_input.predicates)
                if predicate.requirement_id == requirement.requirement_id
            ),
            None,
        )
        for requirement in applicable
    }
    passed = tuple(
        requirement
        for requirement in applicable
        if latest[requirement.requirement_id] is not None
        and latest[requirement.requirement_id].result is PredicateOutcome.PASS
    )
    failed = tuple(
        requirement
        for requirement in applicable
        if latest[requirement.requirement_id] is not None
        and latest[requirement.requirement_id].result is PredicateOutcome.FAIL
    )
    not_evaluated = tuple(
        requirement
        for requirement in applicable
        if latest[requirement.requirement_id] is None
        or latest[requirement.requirement_id].result is PredicateOutcome.NOT_EVALUATED
    )
    pending = tuple(
        decision
        for decision in terminal_input.decisions
        if decision.status is DecisionRequestStatus.PENDING
    )
    failed_ids = {requirement.requirement_id for requirement in (*failed, *not_evaluated)}
    action_remains = any(
        finding.requirement_id in failed_ids
        and finding.status is FindingLifecycle.OPEN
        and finding.has_admissible_option
        for finding in terminal_input.findings
    )
    if pending or action_remains:
        return None

    reasons = tuple(
        f"SPEC_UNRESOLVED: {requirement.requirement_id}"
        for requirement in unresolved
    ) + tuple(
        f"REQUIREMENT_NOT_PASSED: {requirement.requirement_id}"
        for requirement in (*failed, *not_evaluated)
    )
    if not terminal_input.originals_integrity_verified:
        reasons += ("ORIGINAL_INTEGRITY_FAILED",)

    outcome = TerminalOutcome.DELIVERY_READY if not reasons else TerminalOutcome.BLOCKED
    counts = TerminalCounts(
        applicable_blocking=len(applicable),
        passed=len(passed),
        failed=len(failed),
        not_evaluated=len(not_evaluated),
        unresolved_spec=len(unresolved),
        pending_decisions=len(pending),
        autonomous_remediations=terminal_input.autonomous_remediations,
        authorized_remediations=terminal_input.authorized_remediations,
    )
    return TerminalVerdict.model_validate(
        {
            "run_id": terminal_input.run_id,
            "cycle": terminal_input.cycle,
            "outcome": outcome,
            "counts": counts,
            "reasons": reasons,
            "originals_integrity_verified": terminal_input.originals_integrity_verified,
            "computed_at": datetime.now(UTC),
        },
        context={_TERMINAL_CONTEXT_KEY: _TERMINAL_CONTEXT_TOKEN},
    )


def restore_terminal_verdict(value: object) -> TerminalVerdict:
    """Rehydrate a verdict previously minted by ``compute`` from durable state."""

    return TerminalVerdict.model_validate(
        value,
        context={_TERMINAL_CONTEXT_KEY: _TERMINAL_CONTEXT_TOKEN},
    )
