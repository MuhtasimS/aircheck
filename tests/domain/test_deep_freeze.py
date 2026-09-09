"""R2.3 regressions for deeply immutable domain contracts."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import get_args, get_origin

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

import aircheck.domain
from aircheck.domain.models import DeliveryRun
from aircheck.domain.primitives import (
    ActionId,
    ArtifactId,
    AssetId,
    AuthorizationId,
    CandidateId,
    CheckId,
    DecisionId,
    DocumentId,
    EventId,
    EvidenceId,
    FindingId,
    MeasurementId,
    OptionId,
    PlanId,
    PlanItemId,
    ProfileId,
    RequirementId,
    RunId,
    SegmentId,
)
from aircheck.domain.types import ConstraintOperator


MUTABLE_ORIGINS = {list, dict, set}


def _domain_models() -> tuple[type[BaseModel], ...]:
    modules = [aircheck.domain]
    modules.extend(
        importlib.import_module(module.name)
        for module in pkgutil.walk_packages(
            aircheck.domain.__path__,
            prefix=f"{aircheck.domain.__name__}.",
        )
    )
    models = {
        value
        for module in modules
        for _, value in inspect.getmembers(module, inspect.isclass)
        if issubclass(value, BaseModel)
        and value is not BaseModel
        and value.__module__.startswith("aircheck.domain")
    }
    return tuple(sorted(models, key=lambda model: model.__qualname__))


def _mutable_type_paths(annotation: object, path: str) -> tuple[str, ...]:
    origin = get_origin(annotation)
    found = (path,) if origin in MUTABLE_ORIGINS else ()
    nested = tuple(
        child
        for index, argument in enumerate(get_args(annotation))
        for child in _mutable_type_paths(argument, f"{path}[{index}]")
    )
    return found + nested


def test_every_domain_model_field_uses_deeply_frozen_types() -> None:
    """Reintroducing list/dict/set anywhere in a domain DTO must fail D.1.9."""
    violations = [
        path
        for model in _domain_models()
        for field_name, field in model.model_fields.items()
        for path in _mutable_type_paths(field.annotation, f"{model.__name__}.{field_name}")
    ]

    assert violations == []


def test_validated_run_collections_cannot_mutate(
    canonical_run_data: dict[str, object],
) -> None:
    """Validation must not leave mutable nested collections behind."""
    run = DeliveryRun.model_validate(canonical_run_data)

    assert isinstance(run.requirements, tuple)
    assert isinstance(run.requirements[0].applicable_assets, tuple)
    assert isinstance(run.requirements[0].normalized_constraint.fields, tuple)
    assert isinstance(run.findings[0].evidence, tuple)
    assert isinstance(run.events[0].evidence_refs, tuple)

    with pytest.raises((AttributeError, TypeError, ValidationError)):
        run.requirements += (run.requirements[0],)
    with pytest.raises((AttributeError, TypeError, ValidationError)):
        run.events[0].evidence_refs += ("evidence_new",)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("run_id", "delivery_123"),
        ("event_id", "event_123"),
        ("asset_id", "master_123"),
    ],
)
def test_identifier_prefixes_are_strict(
    canonical_run_data: dict[str, object],
    field: str,
    bad_value: str,
) -> None:
    """Cross-kind identifiers must not validate as a different reference type."""
    if field == "run_id":
        canonical_run_data[field] = bad_value
    elif field == "event_id":
        canonical_run_data["events"][0][field] = bad_value  # type: ignore[index]
    else:
        canonical_run_data["original_assets"][0][field] = bad_value  # type: ignore[index]

    with pytest.raises(ValidationError):
        DeliveryRun.model_validate(canonical_run_data)


def test_all_foundation_reference_types_reject_cross_kind_values() -> None:
    """Typed references must not collapse back into interchangeable strings."""
    references = (
        (RunId, "run_001"),
        (DocumentId, "doc_spec"),
        (SegmentId, "doc_spec:s0001"),
        (CandidateId, "candidate_001"),
        (AssetId, "asset_001"),
        (RequirementId, "req_001"),
        (CheckId, "check_001"),
        (PlanId, "plan_001"),
        (PlanItemId, "item_001"),
        (MeasurementId, "measurement_001"),
        (FindingId, "finding_001"),
        (OptionId, "option_001"),
        (DecisionId, "decision_001"),
        (AuthorizationId, "auth_opaque-001"),
        (ActionId, "action_001"),
        (EventId, "evt_001"),
        (EvidenceId, "evidence_001"),
        (ArtifactId, "artifact_001"),
        (ProfileId, "profile_001"),
    )

    for reference_type, valid in references:
        assert TypeAdapter(reference_type).validate_python(valid) == valid
        with pytest.raises(ValidationError):
            TypeAdapter(reference_type).validate_python("wrong_001")


def test_constraint_operator_vocabulary_is_exactly_eight() -> None:
    """The frozen compiler boundary must not acquire speculative operators."""
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
