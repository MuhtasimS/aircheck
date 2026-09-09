"""Immutable AIRCheck lifecycle with typed transition guards."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Final

from pydantic import Field

from aircheck.domain.primitives import (
    AuthorizationId,
    DecisionId,
    FindingId,
    FrozenModel,
    OptionId,
    RunId,
)
from aircheck.domain.status import RunStatus
from aircheck.domain.terminal import TerminalVerdict
from aircheck.domain.types import AuthorizationStatus, DecisionChoice, TerminalOutcome


MAX_CYCLES: Final = 3


class InvalidStateTransition(ValueError):
    def __init__(self, current: RunStatus, target: RunStatus, reason: str = "undeclared edge") -> None:
        self.current = current
        self.target = target
        self.reason = reason
        super().__init__(
            f"Invalid AIRCheck state transition: {current.value} -> {target.value}: {reason}"
        )


class OptionBinding(FrozenModel):
    run_id: RunId
    finding_id: FindingId
    option_id: OptionId
    args_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tier: int = Field(ge=1, le=2)


class RemediationSelection(OptionBinding):
    pass


class AuthorizationBinding(FrozenModel):
    authorization_id: AuthorizationId
    run_id: RunId
    decision_id: DecisionId
    option_id: OptionId
    args_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: AuthorizationStatus


class DecisionResolution(FrozenModel):
    run_id: RunId
    decision_id: DecisionId
    choice: DecisionChoice
    admissible_options_remain: bool


class NoExecutableRequirements(FrozenModel):
    run_id: RunId


class DecisionPending(FrozenModel):
    run_id: RunId
    decision_id: DecisionId
    option: OptionBinding | None = None


class RemediationCompleted(FrozenModel):
    run_id: RunId
    next_cycle: int = Field(ge=1)


class TransitionCause(FrozenModel):
    run_id: RunId
    actor: str
    event_type: str


class RunStateSnapshot(FrozenModel):
    run_id: RunId
    status: RunStatus
    cycle: int = Field(default=0, ge=0)
    admissible_options: tuple[OptionBinding, ...] = ()
    pending_decision_id: DecisionId | None = None
    pending_option: OptionBinding | None = None
    terminal_verdict: TerminalVerdict | None = None


_TRANSITIONS = MappingProxyType(
    {
        RunStatus.CREATED: frozenset({RunStatus.INGESTING}),
        RunStatus.INGESTING: frozenset({RunStatus.INGESTED, RunStatus.FAILED}),
        RunStatus.INGESTED: frozenset({RunStatus.INTERPRETING}),
        RunStatus.INTERPRETING: frozenset(
            {RunStatus.REQUIREMENTS_ADMITTED, RunStatus.FAILED}
        ),
        RunStatus.REQUIREMENTS_ADMITTED: frozenset(
            {RunStatus.QC_PLANNED, RunStatus.BLOCKED}
        ),
        RunStatus.QC_PLANNED: frozenset({RunStatus.INSPECTING}),
        RunStatus.INSPECTING: frozenset(
            {RunStatus.FINDINGS_READY, RunStatus.FAILED}
        ),
        RunStatus.FINDINGS_READY: frozenset(
            {
                RunStatus.DELIVERY_READY,
                RunStatus.BLOCKED,
                RunStatus.REMEDIATING,
                RunStatus.AWAITING_HUMAN_DECISION,
            }
        ),
        RunStatus.REMEDIATING: frozenset({RunStatus.INSPECTING, RunStatus.FAILED}),
        RunStatus.AWAITING_HUMAN_DECISION: frozenset(
            {RunStatus.REMEDIATING, RunStatus.FINDINGS_READY, RunStatus.BLOCKED}
        ),
        RunStatus.DELIVERY_READY: frozenset(),
        RunStatus.BLOCKED: frozenset(),
        RunStatus.FAILED: frozenset(),
    }
)


def can_transition(current: RunStatus, target: RunStatus) -> bool:
    return target in _TRANSITIONS[current]


def _guard(
    run: RunStateSnapshot,
    target: RunStatus,
    payload: Any,
) -> tuple[int, TerminalVerdict | None, DecisionId | None, OptionBinding | None]:
    cycle = run.cycle
    verdict = run.terminal_verdict
    pending_decision = run.pending_decision_id
    pending_option = run.pending_option

    if run.status is RunStatus.FINDINGS_READY and target in {
        RunStatus.DELIVERY_READY,
        RunStatus.BLOCKED,
    }:
        expected = (
            TerminalOutcome.DELIVERY_READY
            if target is RunStatus.DELIVERY_READY
            else TerminalOutcome.BLOCKED
        )
        if not isinstance(payload, TerminalVerdict) or (
            payload.run_id,
            payload.cycle,
            payload.outcome,
        ) != (run.run_id, run.cycle, expected):
            raise InvalidStateTransition(run.status, target, "same-run same-cycle terminal verdict required")
        verdict = payload
    elif run.status is RunStatus.REQUIREMENTS_ADMITTED and target is RunStatus.BLOCKED:
        if not isinstance(payload, NoExecutableRequirements) or payload.run_id != run.run_id:
            raise InvalidStateTransition(run.status, target, "NoExecutableRequirements guard required")
    elif run.status is RunStatus.FINDINGS_READY and target is RunStatus.REMEDIATING:
        if not isinstance(payload, RemediationSelection) or payload.tier != 1:
            raise InvalidStateTransition(run.status, target, "validated Tier-1 option required")
        if not any(
            option == OptionBinding(**payload.model_dump())
            for option in run.admissible_options
        ):
            raise InvalidStateTransition(run.status, target, "Tier-1 option binding mismatch")
    elif run.status is RunStatus.FINDINGS_READY and target is RunStatus.AWAITING_HUMAN_DECISION:
        if not isinstance(payload, DecisionPending) or payload.run_id != run.run_id:
            raise InvalidStateTransition(run.status, target, "DecisionPending guard required")
        if payload.option is not None and payload.option.tier != 2:
            raise InvalidStateTransition(run.status, target, "pending option must be Tier-2")
        pending_decision = payload.decision_id
        pending_option = payload.option
    elif run.status is RunStatus.AWAITING_HUMAN_DECISION and target is RunStatus.REMEDIATING:
        if not isinstance(payload, AuthorizationBinding) or payload.status is not AuthorizationStatus.ISSUED:
            raise InvalidStateTransition(run.status, target, "issued authorization required")
        expected = run.pending_option
        if expected is None or (
            payload.run_id,
            payload.decision_id,
            payload.option_id,
            payload.args_hash,
        ) != (run.run_id, run.pending_decision_id, expected.option_id, expected.args_hash):
            raise InvalidStateTransition(run.status, target, "authorization binding mismatch")
        pending_decision = None
        pending_option = None
    elif run.status is RunStatus.AWAITING_HUMAN_DECISION and target in {
        RunStatus.FINDINGS_READY,
        RunStatus.BLOCKED,
    }:
        expected_remaining = target is RunStatus.FINDINGS_READY
        if not isinstance(payload, DecisionResolution) or (
            payload.run_id,
            payload.decision_id,
            payload.choice,
            payload.admissible_options_remain,
        ) != (
            run.run_id,
            run.pending_decision_id,
            DecisionChoice.DENIED,
            expected_remaining,
        ):
            raise InvalidStateTransition(run.status, target, "matching denied decision required")
        pending_decision = None
        pending_option = None
    elif run.status is RunStatus.REMEDIATING and target is RunStatus.INSPECTING:
        if not isinstance(payload, RemediationCompleted) or payload.run_id != run.run_id:
            raise InvalidStateTransition(run.status, target, "RemediationCompleted guard required")
        if payload.next_cycle != run.cycle + 1 or payload.next_cycle > MAX_CYCLES:
            raise InvalidStateTransition(run.status, target, "remediation cycle limit exceeded")
        cycle = payload.next_cycle
    return cycle, verdict, pending_decision, pending_option


def transition(
    run: RunStateSnapshot,
    target: RunStatus,
    *,
    cause: TransitionCause,
    guard_payload: Any,
) -> RunStateSnapshot:
    if cause.run_id != run.run_id:
        raise InvalidStateTransition(run.status, target, "cause belongs to another run")
    if cause.actor.casefold() == "agent":
        raise InvalidStateTransition(run.status, target, "AGENT cannot cause transitions")
    if not can_transition(run.status, target):
        raise InvalidStateTransition(run.status, target)
    cycle, verdict, pending_decision, pending_option = _guard(run, target, guard_payload)
    return run.model_copy(
        update={
            "status": target,
            "cycle": cycle,
            "terminal_verdict": verdict,
            "pending_decision_id": pending_decision,
            "pending_option": pending_option,
        }
    )


__all__ = (
    "AuthorizationBinding",
    "DecisionPending",
    "DecisionResolution",
    "InvalidStateTransition",
    "MAX_CYCLES",
    "NoExecutableRequirements",
    "OptionBinding",
    "RemediationCompleted",
    "RemediationSelection",
    "RunStateSnapshot",
    "TransitionCause",
    "can_transition",
    "transition",
)
