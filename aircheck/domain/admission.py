"""Deterministic, fail-closed admission of untrusted requirement candidates."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import Field, ValidationInfo, model_validator

from aircheck.domain.catalog import MeasurementDefinition, get_measurement
from aircheck.domain.constraints import (
    Constraint,
    ConstraintDraft,
    EqualsConstraint,
    normalize_constraint,
    render_constraint,
)
from aircheck.domain.primitives import (
    CandidateId,
    FrozenModel,
    NonEmptyStr,
    RequirementId,
    RunId,
    SegmentId,
)
from aircheck.domain.source import SourceDocument, SourceSegment, SourceSpan, build_source_span
from aircheck.domain.types import (
    ApplicabilityStatus,
    AssetRole,
    AutomationDisposition,
    ConditionField,
    ConditionOperator,
    NormalizationStatus,
    ProposedObligation,
    Severity,
)


_REQUIREMENT_CONTEXT_KEY = "aircheck_requirement_factory"
_REQUIREMENT_CONTEXT_TOKEN = object()
_CONDITIONAL_HINT = re.compile(
    r"\b(for|applies? to|only for|except|excluding|does not apply|unless|depending|where appropriate)\b",
    re.IGNORECASE,
)
_PARAMETERIZED_MEASUREMENT = re.compile(
    r"^(?P<base>package\.(?:file_present|filename))\[(?P<role>[A-Z_]+)\]$"
)


class ConstraintAdmissionError(ValueError):
    pass


class ScopeDraft(FrozenModel):
    asset_role: NonEmptyStr
    stream: str | None = None


class ConditionDraft(FrozenModel):
    field: NonEmptyStr
    op: NonEmptyStr
    values: tuple[NonEmptyStr, ...] = Field(min_length=1)
    anchor_segment_id: SegmentId


class CandidateRequirement(FrozenModel):
    candidate_id: CandidateId
    segment_ids: tuple[SegmentId, ...] = Field(min_length=1, max_length=3)
    quote: NonEmptyStr
    proposed_obligation: ProposedObligation
    measurement_key: str | None
    constraint_draft: ConstraintDraft | None
    scope_draft: ScopeDraft
    applicability_draft: tuple[ConditionDraft, ...] = ()
    proposed_status: NormalizationStatus
    reason: NonEmptyStr
    confidence: float = Field(ge=0, le=1)
    proposed_disposition: str | None = None
    referenced_document: str | None = None


class Scope(FrozenModel):
    asset_role: AssetRole
    stream: str | None = None


class Condition(FrozenModel):
    field: ConditionField
    op: ConditionOperator
    values: tuple[NonEmptyStr, ...] = Field(min_length=1)
    anchor_segment_id: SegmentId


class Verification(FrozenModel):
    measurement_key: NonEmptyStr
    tool: NonEmptyStr


class Requirement(FrozenModel):
    requirement_id: RequirementId
    run_id: RunId
    span: SourceSpan
    normalization_status: NormalizationStatus
    status_reasons: tuple[NonEmptyStr, ...]
    severity: Severity
    severity_source: NonEmptyStr
    scope: Scope
    applicability: tuple[Condition, ...]
    applicability_status: ApplicabilityStatus
    constraint: Constraint | None
    verification: Verification | None
    automation_disposition: AutomationDisposition
    rendered_text: NonEmptyStr
    candidate_ref: CandidateId
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def require_admission_factory(cls, value: Any, info: ValidationInfo) -> Any:
        context = info.context or {}
        if context.get(_REQUIREMENT_CONTEXT_KEY) is not _REQUIREMENT_CONTEXT_TOKEN:
            raise TypeError("Requirement must be created by admit()")
        return value


class ExecutableRequirement(Requirement):
    pass


def modal_severity(text: str) -> tuple[Severity, str]:
    lowered = text.casefold()
    groups = (
        (Severity.BLOCKING, ("must not", "shall not", "must", "shall", "required")),
        (Severity.WARNING, ("should", "recommended")),
        (Severity.INFO, ("may", "optional")),
    )
    matches = tuple(
        (severity, token)
        for severity, tokens in groups
        for token in tokens
        if re.search(rf"\b{re.escape(token)}\b", lowered)
    )
    severities = {severity for severity, _ in matches}
    if not matches:
        return Severity.BLOCKING, "UNKNOWN"
    if len(severities) > 1:
        return Severity.BLOCKING, "MIXED"
    return matches[0]


def _condition_supported(text: str, draft: ConditionDraft) -> bool:
    lowered = " ".join(text.casefold().split())
    values = tuple(" ".join(value.casefold().replace("_", " ").split()) for value in draft.values)
    try:
        operator = ConditionOperator(draft.op)
    except ValueError:
        return False
    if operator is ConditionOperator.EQ:
        return any(
            phrase in lowered
            for value in values
            for phrase in (f"for {value}", f"applies to {value}", f"only for {value}")
        )
    if operator is ConditionOperator.NEQ:
        return any(
            phrase in lowered
            for value in values
            for phrase in (f"except {value}", f"excluding {value}", f"does not apply to {value}")
        )
    return "one of" in lowered and all(value in lowered for value in values)


def _admit_applicability(
    candidate: CandidateRequirement,
    span: SourceSpan,
    segments: tuple[SourceSegment, ...],
) -> tuple[tuple[Condition, ...], ApplicabilityStatus, tuple[str, ...]]:
    has_hint = bool(_CONDITIONAL_HINT.search(span.text)) or (
        bool(candidate.applicability_draft)
        and bool(re.search(r"\bone of\b", span.text, re.IGNORECASE))
    )
    if not candidate.applicability_draft:
        if has_hint:
            return (), ApplicabilityStatus.UNRESOLVED, ("APPLICABILITY_UNRESOLVED",)
        return (), ApplicabilityStatus.APPLICABLE, ()
    if not has_hint:
        return (), ApplicabilityStatus.APPLICABLE, ("SPURIOUS_APPLICABILITY_IGNORED",)

    by_id = {segment.segment_id: segment for segment in segments}
    trusted: list[Condition] = []
    for draft in candidate.applicability_draft:
        if draft.anchor_segment_id not in span.segment_ids or draft.anchor_segment_id not in by_id:
            return (), ApplicabilityStatus.UNRESOLVED, ("APPLICABILITY_ANCHOR_INVALID",)
        try:
            field = ConditionField(draft.field)
        except ValueError:
            return (), ApplicabilityStatus.UNRESOLVED, ("UNSUPPORTED_APPLICABILITY_FIELD",)
        try:
            operator = ConditionOperator(draft.op)
        except ValueError:
            return (), ApplicabilityStatus.UNRESOLVED, ("UNSUPPORTED_APPLICABILITY_OPERATOR",)
        if not _condition_supported(by_id[draft.anchor_segment_id].text, draft):
            return (), ApplicabilityStatus.UNRESOLVED, ("APPLICABILITY_POLARITY_UNPROVEN",)
        trusted.append(
            Condition(
                field=field,
                op=operator,
                values=draft.values,
                anchor_segment_id=draft.anchor_segment_id,
            )
        )
    return tuple(trusted), ApplicabilityStatus.APPLICABLE, ()


def _disposition(
    definition: MeasurementDefinition | None,
    proposed: str | None,
) -> tuple[AutomationDisposition, tuple[str, ...]]:
    floor = definition.remediation_default if definition else AutomationDisposition.FORBIDDEN
    if proposed is None:
        return floor, ()
    try:
        requested = AutomationDisposition(proposed)
    except ValueError:
        return floor, ("UNKNOWN_DISPOSITION_IGNORED",)
    order = {
        AutomationDisposition.AUTO_REMEDIATE: 0,
        AutomationDisposition.REPORT_ONLY: 1,
        AutomationDisposition.HUMAN_APPROVAL_REQUIRED: 2,
        AutomationDisposition.FORBIDDEN: 3,
    }
    if order[requested] < order[floor]:
        return floor, ("DISPOSITION_LOOSENING_IGNORED",)
    return requested, ()


def _measurement_key(
    proposed: str,
    scope: Scope,
) -> tuple[str | None, tuple[str, ...]]:
    match = _PARAMETERIZED_MEASUREMENT.fullmatch(proposed)
    if match is None:
        return proposed, ()
    if match.group("role") != scope.asset_role.value:
        return None, ("PARAMETERIZED_MEASUREMENT_ROLE_MISMATCH",)
    return (
        f"{match.group('base')}[role]",
        ("PARAMETERIZED_MEASUREMENT_KEY_NORMALIZED",),
    )


def _source_owned_value_normalization(
    constraint: Constraint | None,
    span: SourceSpan,
) -> tuple[Constraint | None, tuple[str, ...]]:
    if (
        isinstance(constraint, EqualsConstraint)
        and constraint.measurement_key == "container.format"
        and constraint.value == "quicktime"
        and re.search(r"\bquicktime\s+mov\b", span.text, re.IGNORECASE)
    ):
        return (
            constraint.model_copy(update={"value": "quicktime mov"}),
            ("SOURCE_ENUM_VALUE_NORMALIZED",),
        )
    return constraint, ()


def _requirement_id(
    document: SourceDocument,
    span: SourceSpan,
    constraint: Constraint | None,
    status: NormalizationStatus,
) -> str:
    canonical = (
        json.dumps(constraint.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        if constraint
        else status.value
    )
    payload = "|".join((document.normalized_sha256, *span.segment_ids, canonical))
    return f"req_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def admit(
    candidate: CandidateRequirement,
    document: SourceDocument,
    segments: tuple[SourceSegment, ...],
    *,
    available_document_titles: tuple[str, ...] = (),
) -> Requirement:
    span = build_source_span(
        document,
        segments,
        candidate.segment_ids,
        quote=candidate.quote,
    )
    severity, severity_source = modal_severity(span.text)
    reasons: tuple[str, ...] = ()

    try:
        scope = Scope(
            asset_role=AssetRole(candidate.scope_draft.asset_role),
            stream=candidate.scope_draft.stream,
        )
    except ValueError:
        scope = Scope(asset_role=AssetRole.OTHER, stream=candidate.scope_draft.stream)
        reasons += ("UNKNOWN_ASSET_ROLE",)

    definition: MeasurementDefinition | None = None
    measurement_key = candidate.measurement_key
    key_mismatch = False
    if measurement_key is not None:
        measurement_key, key_reasons = _measurement_key(measurement_key, scope)
        reasons += key_reasons
        key_mismatch = measurement_key is None
    if key_mismatch:
        constraint = None
        status = NormalizationStatus.UNSUPPORTED
    elif measurement_key is None:
        constraint = None
        status = NormalizationStatus.AMBIGUOUS
        reasons += ("MISSING_MEASUREMENT_KEY",)
    else:
        try:
            definition = get_measurement(measurement_key)
        except KeyError:
            constraint = None
            status = NormalizationStatus.UNSUPPORTED
            reasons += ("UNKNOWN_MEASUREMENT_KEY",)
        else:
            constraint, status, constraint_reasons = normalize_constraint(
                candidate.constraint_draft,
                definition,
            )
            reasons += constraint_reasons
            constraint, value_reasons = _source_owned_value_normalization(
                constraint,
                span,
            )
            reasons += value_reasons

    if (
        definition is not None
        and definition.produced_by == "measure_loudness"
        and scope.stream is None
        and status is NormalizationStatus.EXECUTABLE
    ):
        status = NormalizationStatus.AMBIGUOUS
        constraint = None
        reasons += ("MISSING_REQUIRED_STREAM",)

    applicability, applicability_status, applicability_reasons = _admit_applicability(
        candidate,
        span,
        segments,
    )
    reasons += applicability_reasons

    if candidate.referenced_document and candidate.referenced_document.casefold() not in {
        title.casefold() for title in available_document_titles
    }:
        status = NormalizationStatus.EXTERNAL_DEPENDENCY
        constraint = None
        reasons += ("REFERENCED_DOCUMENT_ABSENT",)
    elif status is NormalizationStatus.EXECUTABLE and candidate.proposed_status is not NormalizationStatus.EXECUTABLE:
        status = candidate.proposed_status
        constraint = None
        reasons += ("MODEL_STATUS_CEILING",)

    if "UNKNOWN_ASSET_ROLE" in reasons:
        status = NormalizationStatus.UNSUPPORTED
        constraint = None
    if (
        status is not NormalizationStatus.EXECUTABLE
        and applicability_status is ApplicabilityStatus.APPLICABLE
    ):
        applicability_status = ApplicabilityStatus.UNRESOLVED
        if "APPLICABILITY_UNRESOLVED" not in reasons:
            reasons += ("APPLICABILITY_UNRESOLVED",)

    disposition, disposition_reasons = _disposition(definition, candidate.proposed_disposition)
    reasons += disposition_reasons
    verification = (
        Verification(measurement_key=definition.measurement_key, tool=definition.produced_by)
        if definition
        else None
    )
    if constraint is not None and status is NormalizationStatus.EXECUTABLE:
        ref = f"{document.title} {span.reference}" if span.reference else f"{document.title} (clause at {span.start}-{span.end})"
        rendered = f"{ref} requires {render_constraint(constraint, label=definition.label)}."
        if applicability:
            condition = applicability[0]
            relation = {
                ConditionOperator.EQ: "is",
                ConditionOperator.NEQ: "is not",
                ConditionOperator.IN: "is one of",
            }[condition.op]
            rendered += f" This applies when {condition.field.value} {relation} {', '.join(condition.values)}."
    else:
        rendered = (
            f"{document.title} could not be made executable: "
            f"{', '.join(reasons) or status.value}."
        )

    model = ExecutableRequirement if status is NormalizationStatus.EXECUTABLE else Requirement
    return model.model_validate(
        {
            "requirement_id": _requirement_id(document, span, constraint, status),
            "run_id": document.run_id,
            "span": span,
            "normalization_status": status,
            "status_reasons": reasons,
            "severity": severity,
            "severity_source": severity_source,
            "scope": scope,
            "applicability": applicability,
            "applicability_status": applicability_status,
            "constraint": constraint,
            "verification": verification,
            "automation_disposition": disposition,
            "rendered_text": rendered,
            "candidate_ref": candidate.candidate_id,
            "confidence": candidate.confidence,
        },
        context={_REQUIREMENT_CONTEXT_KEY: _REQUIREMENT_CONTEXT_TOKEN},
    )


def find_contradictions(
    requirements: tuple[Requirement, ...],
) -> tuple[tuple[str, str], ...]:
    contradictions: list[tuple[str, str]] = []
    for index, left in enumerate(requirements):
        for right in requirements[index + 1 :]:
            if (
                left.normalization_status is NormalizationStatus.EXECUTABLE
                and right.normalization_status is NormalizationStatus.EXECUTABLE
                and isinstance(left.constraint, EqualsConstraint)
                and isinstance(right.constraint, EqualsConstraint)
                and left.constraint.measurement_key == right.constraint.measurement_key
                and left.scope == right.scope
                and left.applicability == right.applicability
                and left.constraint.value != right.constraint.value
            ):
                contradictions.append((left.requirement_id, right.requirement_id))
    return tuple(contradictions)


def mark_contradictions(
    requirements: tuple[Requirement, ...],
) -> tuple[Requirement, ...]:
    """Downgrade the small provable equality-conflict subset fail closed."""

    peers: dict[str, list[str]] = {}
    for left, right in find_contradictions(requirements):
        peers.setdefault(left, []).append(right)
        peers.setdefault(right, []).append(left)

    marked: list[Requirement] = []
    for requirement in requirements:
        if requirement.requirement_id not in peers:
            marked.append(requirement)
            continue
        values = dict(requirement.__dict__)
        values.update(
            normalization_status=NormalizationStatus.CONTRADICTORY,
            status_reasons=requirement.status_reasons
            + ("CONTRADICTS_REQUIREMENT", *tuple(peers[requirement.requirement_id])),
            constraint=None,
            rendered_text=(
                f"Conflicting executable requirements: "
                f"{', '.join(peers[requirement.requirement_id])}."
            ),
        )
        marked.append(
            Requirement.model_validate(
                values,
                context={_REQUIREMENT_CONTEXT_KEY: _REQUIREMENT_CONTEXT_TOKEN},
            )
        )
    return tuple(marked)
