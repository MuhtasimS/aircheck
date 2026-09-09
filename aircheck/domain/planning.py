"""Deterministic required-check derivation and complete-plan validation."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import Field, ValidationError, ValidationInfo, model_validator

from aircheck.domain.admission import ExecutableRequirement, Requirement
from aircheck.domain.assets import Asset
from aircheck.domain.primitives import (
    AssetId,
    CanonicalScalar,
    CheckId,
    FrozenModel,
    NonEmptyStr,
    PlanId,
    PlanItemId,
    RequirementId,
    RunId,
)
from aircheck.domain.types import ApplicabilityStatus, AuthorityTier, PlanSource
from aircheck.tools import TOOL_REGISTRY


_PLAN_CONTEXT_KEY = "aircheck_plan_validator"
_PLAN_CONTEXT_TOKEN = object()


class PlanValidationError(ValueError):
    pass


class ToolArgument(FrozenModel):
    name: NonEmptyStr
    value: CanonicalScalar


class RequiredCheck(FrozenModel):
    check_id: CheckId
    requirement_id: RequirementId
    asset_id: AssetId
    tool: NonEmptyStr
    tool_args: tuple[ToolArgument, ...]


class ExtraCheck(FrozenModel):
    item_id: PlanItemId
    tool: NonEmptyStr
    asset_id: AssetId
    tool_args: tuple[ToolArgument, ...] = ()
    rationale: NonEmptyStr
    motivated_by: NonEmptyStr


class PlanGroup(FrozenModel):
    label: NonEmptyStr
    check_ids: tuple[CheckId, ...] = Field(min_length=1)


class PlanProposal(FrozenModel):
    ordered_items: tuple[str, ...]
    extras: tuple[ExtraCheck, ...] = ()
    groups: tuple[PlanGroup, ...] = ()
    strategy_note: str | None = None


class QCPlanItem(FrozenModel):
    item_id: PlanItemId
    check_id: CheckId | None
    requirement_id: RequirementId | None
    asset_id: AssetId
    tool: NonEmptyStr
    tool_args: tuple[ToolArgument, ...]
    order: int = Field(ge=0)
    rationale: str | None = None


class QCPlan(FrozenModel):
    plan_id: PlanId
    run_id: RunId
    cycle: int = Field(ge=0)
    items: tuple[QCPlanItem, ...]
    strategy_note: str | None = None
    source: PlanSource

    @model_validator(mode="before")
    @classmethod
    def require_validator(cls, value: Any, info: ValidationInfo) -> Any:
        context = info.context or {}
        if context.get(_PLAN_CONTEXT_KEY) is not _PLAN_CONTEXT_TOKEN:
            raise TypeError("QCPlan must be created by validate_plan()")
        return value


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def derive_required_checks(
    requirements: tuple[Requirement, ...],
    assets: tuple[Asset, ...],
) -> tuple[RequiredCheck, ...]:
    checks: list[RequiredCheck] = []
    for requirement in requirements:
        if not isinstance(requirement, ExecutableRequirement):
            continue
        if requirement.applicability_status is not ApplicabilityStatus.APPLICABLE:
            continue
        if requirement.verification is None:
            raise PlanValidationError("executable requirement has no verification binding")
        specification = TOOL_REGISTRY.get(requirement.verification.tool)
        if specification is None or specification.authority_tier is not AuthorityTier.INSPECT:
            raise PlanValidationError("required checks must use a registered Tier-0 tool")
        fields = specification.input_model.model_fields
        run_assets = tuple(asset for asset in assets if asset.run_id == requirement.run_id)
        if "asset_id" in fields:
            bound_assets = tuple(
                asset
                for asset in run_assets
                if asset.role is requirement.scope.asset_role
            )
        else:
            # Package-level tools inspect absence as a fact. Binding their check to
            # an existing run asset keeps the plan typed without making a fake
            # record for a component that is not present.
            scoped_assets = tuple(
                asset
                for asset in run_assets
                if asset.role is requirement.scope.asset_role
            )
            bound_assets = scoped_assets[:1] or run_assets[:1]
        for asset in bound_assets:
            fields = specification.input_model.model_fields
            arguments: list[ToolArgument] = []
            if "run_id" in fields:
                arguments.append(ToolArgument(name="run_id", value=requirement.run_id))
            if "asset_id" in fields:
                arguments.append(ToolArgument(name="asset_id", value=asset.asset_id))
            if "stream" in fields:
                if requirement.scope.stream is None:
                    raise PlanValidationError("required check tool requires a source-backed stream")
                arguments.append(ToolArgument(name="stream", value=requirement.scope.stream))
            check_id = _stable_id(
                "check",
                requirement.requirement_id,
                asset.asset_id,
                requirement.verification.tool,
            )
            checks.append(
                RequiredCheck(
                    check_id=check_id,
                    requirement_id=requirement.requirement_id,
                    asset_id=asset.asset_id,
                    tool=requirement.verification.tool,
                    tool_args=tuple(arguments),
                )
            )
    return tuple(checks)


def _fingerprint(tool: str, asset_id: str, args: tuple[ToolArgument, ...]) -> tuple[object, ...]:
    explicit_args = (
        (argument.name, argument.value)
        for argument in args
        if argument.name not in {"asset_id", "run_id"}
    )
    return (tool, asset_id, *explicit_args)


def _validate_tool_arguments(
    *,
    run_id: str,
    asset_id: str,
    tool: str,
    arguments: tuple[ToolArgument, ...],
) -> None:
    specification = TOOL_REGISTRY[tool]
    names = tuple(argument.name for argument in arguments)
    if len(names) != len(set(names)):
        raise PlanValidationError("tool arguments must have unique names")
    fields = specification.input_model.model_fields
    payload = {argument.name: argument.value for argument in arguments}
    if "run_id" in fields:
        if "run_id" in payload and payload["run_id"] != run_id:
            raise PlanValidationError("tool arguments reference another run")
        payload["run_id"] = run_id
    if "asset_id" in fields:
        if "asset_id" in payload and payload["asset_id"] != asset_id:
            raise PlanValidationError("tool arguments reference another asset")
        payload["asset_id"] = asset_id
    try:
        specification.input_model.model_validate(payload)
    except (TypeError, ValueError, ValidationError) as exc:
        raise PlanValidationError("extra check has invalid typed arguments") from exc


def _build_plan(
    *,
    run_id: str,
    cycle: int,
    proposal: PlanProposal,
    required_checks: tuple[RequiredCheck, ...],
    source: PlanSource,
) -> QCPlan:
    required_by_id = {check.check_id: check for check in required_checks}
    extras_by_id = {extra.item_id: extra for extra in proposal.extras}
    items: list[QCPlanItem] = []
    for order, item_id in enumerate(proposal.ordered_items):
        if item_id in required_by_id:
            check = required_by_id[item_id]
            items.append(
                QCPlanItem(
                    item_id=_stable_id("item", run_id, str(cycle), item_id),
                    check_id=check.check_id,
                    requirement_id=check.requirement_id,
                    asset_id=check.asset_id,
                    tool=check.tool,
                    tool_args=check.tool_args,
                    order=order,
                )
            )
        else:
            extra = extras_by_id[item_id]
            items.append(
                QCPlanItem(
                    item_id=extra.item_id,
                    check_id=None,
                    requirement_id=None,
                    asset_id=extra.asset_id,
                    tool=extra.tool,
                    tool_args=extra.tool_args,
                    order=order,
                    rationale=extra.rationale,
                )
            )
    plan_id = _stable_id("plan", run_id, str(cycle), *proposal.ordered_items)
    return QCPlan.model_validate(
        {
            "plan_id": plan_id,
            "run_id": run_id,
            "cycle": cycle,
            "items": tuple(items),
            "strategy_note": proposal.strategy_note,
            "source": source,
        },
        context={_PLAN_CONTEXT_KEY: _PLAN_CONTEXT_TOKEN},
    )


def validate_plan(
    *,
    run_id: str,
    cycle: int,
    proposal: PlanProposal,
    required_checks: tuple[RequiredCheck, ...],
    assets: tuple[Asset, ...],
) -> QCPlan:
    required_by_id = {check.check_id: check for check in required_checks}
    extras_by_id = {extra.item_id: extra for extra in proposal.extras}
    expected_ids = tuple(required_by_id) + tuple(extras_by_id)
    if len(expected_ids) != len(required_checks) + len(proposal.extras):
        raise PlanValidationError("plan item identifiers must be unique")
    if sorted(proposal.ordered_items) != sorted(expected_ids):
        raise PlanValidationError("every required check and extra must appear exactly once")
    if len(proposal.extras) > len(required_checks):
        raise PlanValidationError("extra checks cannot outnumber required checks")

    grouped_check_ids = tuple(
        check_id for group in proposal.groups for check_id in group.check_ids
    )
    if len(grouped_check_ids) != len(set(grouped_check_ids)):
        raise PlanValidationError("plan groups cannot repeat a required check")
    if set(grouped_check_ids) - set(required_by_id):
        raise PlanValidationError("plan group references an unknown required check")

    asset_ids = {asset.asset_id for asset in assets if asset.run_id == run_id}
    if len(asset_ids) != len(tuple(asset for asset in assets if asset.run_id == run_id)):
        raise PlanValidationError("run asset identifiers must be unique")
    fingerprints = {_fingerprint(check.tool, check.asset_id, check.tool_args) for check in required_checks}
    required_requirement_ids = {
        check.requirement_id for check in required_checks
    }
    for extra in proposal.extras:
        specification = TOOL_REGISTRY.get(extra.tool)
        if specification is None or specification.authority_tier is not AuthorityTier.INSPECT:
            raise PlanValidationError("extra checks must use a Tier-0 tool")
        if extra.asset_id not in asset_ids:
            raise PlanValidationError("extra check references an asset outside the run")
        if extra.motivated_by not in required_requirement_ids:
            raise PlanValidationError("extra check must cite a required requirement")
        _validate_tool_arguments(
            run_id=run_id,
            asset_id=extra.asset_id,
            tool=extra.tool,
            arguments=extra.tool_args,
        )
        fingerprint = _fingerprint(extra.tool, extra.asset_id, extra.tool_args)
        if fingerprint in fingerprints:
            raise PlanValidationError("plan contains a duplicate tool/asset/args operation")
        fingerprints.add(fingerprint)

    return _build_plan(
        run_id=run_id,
        cycle=cycle,
        proposal=proposal,
        required_checks=required_checks,
        source=PlanSource.PROPOSAL,
    )


def deterministic_fallback_plan(
    *,
    run_id: str,
    cycle: int,
    required_checks: tuple[RequiredCheck, ...],
    assets: tuple[Asset, ...],
) -> QCPlan:
    """Construct the stable complete plan used after bounded S2 rejection."""

    asset_ids = {asset.asset_id for asset in assets if asset.run_id == run_id}
    if any(check.asset_id not in asset_ids for check in required_checks):
        raise PlanValidationError("required check references an asset outside the run")
    tool_order = {name: index for index, name in enumerate(TOOL_REGISTRY)}
    ordered = tuple(
        check.check_id
        for check in sorted(
            required_checks,
            key=lambda check: (
                tool_order.get(check.tool, len(tool_order)),
                check.asset_id,
                check.check_id,
            ),
        )
    )
    proposal = PlanProposal(
        ordered_items=ordered,
        strategy_note="Deterministic required-check fallback.",
    )
    return _build_plan(
        run_id=run_id,
        cycle=cycle,
        proposal=proposal,
        required_checks=required_checks,
        source=PlanSource.FALLBACK,
    )
