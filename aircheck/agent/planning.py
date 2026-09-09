"""Bounded S2 plan proposal with deterministic validation and fallback."""

from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError

from aircheck.agent.contracts import StructuredSemanticModel
from aircheck.agent.prompts import build_planning_prompt
from aircheck.domain.assets import Asset
from aircheck.domain.planning import (
    PlanProposal,
    PlanValidationError,
    QCPlan,
    RequiredCheck,
    deterministic_fallback_plan,
    validate_plan,
)
from aircheck.domain.primitives import FrozenModel, NonEmptyStr


class PlanningResult(FrozenModel):
    plan: QCPlan
    attempts: int = Field(ge=1, le=2)
    validation_failures: tuple[NonEmptyStr, ...] = ()


def _validate_proposal(value: Any) -> PlanProposal:
    if isinstance(value, PlanProposal):
        return value
    if isinstance(value, str):
        return PlanProposal.model_validate_json(value)
    return PlanProposal.model_validate(value)


def construct_plan(
    model: StructuredSemanticModel,
    *,
    run_id: str,
    cycle: int,
    required_checks: tuple[RequiredCheck, ...],
    assets: tuple[Asset, ...],
    max_attempts: int = 2,
) -> PlanningResult:
    """Ask S2 for strategy at most twice; coverage authority never leaves runtime."""

    if not 1 <= max_attempts <= 2:
        raise ValueError("S2 permits at most 2 attempts")
    failures: list[str] = []
    asset_ids = tuple(
        sorted(asset.asset_id for asset in assets if asset.run_id == run_id)
    )
    for attempt in range(1, max_attempts + 1):
        prompt = build_planning_prompt(
            run_id=run_id,
            cycle=cycle,
            required_checks=required_checks,
            asset_ids=asset_ids,
            retry_errors=tuple(failures[-1:]),
        )
        try:
            raw = model.generate(prompt, PlanProposal)
        except Exception as exc:
            failures.append(f"PROVIDER_ERROR:{type(exc).__name__}")
            continue
        try:
            proposal = _validate_proposal(raw)
        except (TypeError, ValueError, ValidationError):
            failures.append("SCHEMA_INVALID")
            continue
        try:
            plan = validate_plan(
                run_id=run_id,
                cycle=cycle,
                proposal=proposal,
                required_checks=required_checks,
                assets=assets,
            )
        except PlanValidationError as exc:
            failures.append(f"PLAN_REJECTED:{exc}")
            continue
        return PlanningResult(
            plan=plan,
            attempts=attempt,
            validation_failures=tuple(failures),
        )

    return PlanningResult(
        plan=deterministic_fallback_plan(
            run_id=run_id,
            cycle=cycle,
            required_checks=required_checks,
            assets=assets,
        ),
        attempts=max_attempts,
        validation_failures=tuple(failures),
    )
