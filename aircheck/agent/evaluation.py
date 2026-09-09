"""Deterministic evaluators for M3 normalization ground truth."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aircheck.domain.admission import Requirement
from aircheck.domain.catalog import get_measurement
from aircheck.domain.constraints import (
    AbsentConstraint,
    EqualsConstraint,
    MaxConstraint,
    MinConstraint,
    OneOfConstraint,
    PatternConstraint,
    PresentConstraint,
    RangeConstraint,
    render_constraint,
)
from aircheck.domain.primitives import FrozenModel, NonEmptyStr
from aircheck.domain.types import (
    ApplicabilityStatus,
    NormalizationStatus,
    Severity,
)


class EvaluationAssessment(FrozenModel):
    passed: bool
    errors: tuple[NonEmptyStr, ...]
    reason_codes: tuple[str, ...] = ()
    terminal_evaluated: bool = False


def _constraint_signature(requirement: Requirement) -> dict[str, Any] | None:
    constraint = requirement.constraint
    if constraint is None:
        return None
    signature: dict[str, Any] = {"operator": constraint.operator.value}
    if constraint.unit is not None:
        signature["unit"] = constraint.unit
    if isinstance(constraint, EqualsConstraint):
        signature["value"] = constraint.value
    elif isinstance(constraint, (MinConstraint, MaxConstraint)):
        signature["value"] = constraint.value
    elif isinstance(constraint, RangeConstraint):
        signature.update(lower=constraint.lower, upper=constraint.upper)
    elif isinstance(constraint, OneOfConstraint):
        signature["values"] = list(constraint.values)
    elif isinstance(constraint, PatternConstraint):
        signature.update(
            pattern=constraint.pattern,
            description=constraint.description,
        )
    elif isinstance(constraint, PresentConstraint):
        signature["value"] = True
    elif isinstance(constraint, AbsentConstraint):
        signature["value"] = False
    return signature


def evaluate_product_profile(
    requirements: tuple[Requirement, ...],
    profile: Mapping[str, Any],
) -> EvaluationAssessment:
    """Compare admitted typed predicates with frozen M2 product expectations."""

    errors: list[str] = []
    by_key: dict[str, Requirement] = {}
    for requirement in requirements:
        if requirement.normalization_status is not NormalizationStatus.EXECUTABLE:
            errors.append(
                f"{requirement.candidate_ref}: {requirement.normalization_status.value}"
            )
            continue
        if requirement.constraint is None:
            errors.append(f"{requirement.candidate_ref}: executable without constraint")
            continue
        key = requirement.constraint.measurement_key
        if key in by_key:
            errors.append(f"duplicate measurement requirement: {key}")
        by_key[key] = requirement
        if requirement.severity is not Severity.BLOCKING:
            errors.append(f"{key}: valid profile requirement is not BLOCKING")
        if requirement.applicability_status is not ApplicabilityStatus.APPLICABLE:
            errors.append(f"{key}: valid profile requirement is not APPLICABLE")
        expected_render = render_constraint(
            requirement.constraint,
            label=get_measurement(key).label,
        )
        if expected_render not in requirement.rendered_text:
            errors.append(f"{key}: rendered predicate does not round trip")

    expected_requirements = profile["requirements"]
    if set(by_key) != set(expected_requirements):
        missing = sorted(set(expected_requirements) - set(by_key))
        unexpected = sorted(set(by_key) - set(expected_requirements))
        errors.append(f"measurement set mismatch: missing={missing} unexpected={unexpected}")
    for key, expected in expected_requirements.items():
        requirement = by_key.get(key)
        if requirement is None:
            continue
        observed = _constraint_signature(requirement)
        if observed != expected:
            errors.append(f"{key}: expected {expected!r}, observed {observed!r}")
    return EvaluationAssessment(passed=not errors, errors=tuple(errors))


def _reason_codes(requirements: tuple[Requirement, ...]) -> tuple[str, ...]:
    statuses = {requirement.normalization_status for requirement in requirements}
    reasons = {
        reason
        for requirement in requirements
        for reason in requirement.status_reasons
    }
    if NormalizationStatus.CONTRADICTORY in statuses:
        return ("CONTRADICTORY_CONSTRAINTS",)
    if NormalizationStatus.EXTERNAL_DEPENDENCY in statuses:
        return ("EXTERNAL_REFERENCE_REQUIRED",)
    if "UNSUPPORTED_APPLICABILITY_FIELD" in reasons:
        return ("UNSUPPORTED_APPLICABILITY_FIELD",)
    if any(requirement.severity_source == "MIXED" for requirement in requirements):
        return ("MIXED_MODAL_LANGUAGE",)
    if statuses == {NormalizationStatus.AMBIGUOUS}:
        return ("AMBIGUOUS_CONSTRAINT",)
    if statuses == {NormalizationStatus.UNSUPPORTED}:
        if all(requirement.severity_source == "UNKNOWN" for requirement in requirements):
            return ("UNTRUSTED_DIRECTIVE_NO_EXECUTABLE_REQUIREMENT",)
        if "UNKNOWN_MEASUREMENT_KEY" in reasons:
            return ("UNKNOWN_MEASUREMENT_KEY",)
    return tuple(sorted(reasons))


def evaluate_adversarial_outcome(
    requirements: tuple[Requirement, ...],
    expected: Mapping[str, Any],
) -> EvaluationAssessment:
    """Evaluate the four M3-owned dimensions; terminal behavior remains M4-owned."""

    errors: list[str] = []
    observed_statuses = {
        requirement.normalization_status.value for requirement in requirements
    }
    observed_applicability = {
        requirement.applicability_status.value for requirement in requirements
    }
    observed_severity = {requirement.severity.value for requirement in requirements}
    comparisons = (
        ("normalization_status", observed_statuses, expected["expected_normalization_status"]),
        ("applicability_status", observed_applicability, expected["expected_applicability_status"]),
        ("severity", observed_severity, expected["expected_severity"]),
    )
    for label, observed, wanted in comparisons:
        if observed != {wanted}:
            errors.append(f"{label}: expected {wanted}, observed {sorted(observed)}")
    reason_codes = _reason_codes(requirements)
    if set(reason_codes) != set(expected["reason_codes"]):
        errors.append(
            f"reason_codes: expected {expected['reason_codes']!r}, observed {reason_codes!r}"
        )
    return EvaluationAssessment(
        passed=not errors,
        errors=tuple(errors),
        reason_codes=reason_codes,
        terminal_evaluated=False,
    )
