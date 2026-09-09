"""R2.6 lifecycle, guard, and graph-hardening tests."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from aircheck.domain import state_machine
from aircheck.domain.state_machine import (
    AuthorizationBinding,
    DecisionPending,
    DecisionResolution,
    InvalidStateTransition,
    NoExecutableRequirements,
    OptionBinding,
    RemediationCompleted,
    RemediationSelection,
    RunStateSnapshot,
    TransitionCause,
    can_transition,
    transition,
)
from aircheck.domain.status import RunStatus, TERMINAL_STATUSES
from aircheck.domain.terminal import TerminalInput, compute
from aircheck.domain.types import AuthorizationStatus, DecisionChoice, TerminalOutcome


EXPECTED_GRAPH = {
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
    RunStatus.INSPECTING: frozenset({RunStatus.FINDINGS_READY, RunStatus.FAILED}),
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


def _run(status: RunStatus, *, cycle: int = 0) -> RunStateSnapshot:
    return RunStateSnapshot(run_id="run_lifecycle_001", status=status, cycle=cycle)


def _cause(event_type: str = "RUNTIME_TRANSITION") -> TransitionCause:
    return TransitionCause(
        run_id="run_lifecycle_001",
        actor="SYSTEM",
        event_type=event_type,
    )


def test_status_vocabulary_and_reachability_matrix_are_exact() -> None:
    assert tuple(status.value for status in RunStatus) == (
        "CREATED",
        "INGESTING",
        "INGESTED",
        "INTERPRETING",
        "REQUIREMENTS_ADMITTED",
        "QC_PLANNED",
        "INSPECTING",
        "FINDINGS_READY",
        "REMEDIATING",
        "AWAITING_HUMAN_DECISION",
        "DELIVERY_READY",
        "BLOCKED",
        "FAILED",
    )
    actual = {
        source: frozenset(target for target in RunStatus if can_transition(source, target))
        for source in RunStatus
    }
    assert actual == EXPECTED_GRAPH


def test_transition_graph_is_private_and_kepler_mutation_raises() -> None:
    assert "TRANSITIONS" not in state_machine.__all__
    assert isinstance(state_machine._TRANSITIONS, MappingProxyType)
    assert all(isinstance(targets, frozenset) for targets in state_machine._TRANSITIONS.values())

    with pytest.raises(TypeError):
        state_machine._TRANSITIONS[RunStatus.CREATED] |= frozenset(
            {RunStatus.DELIVERY_READY}
        )
    assert not can_transition(RunStatus.CREATED, RunStatus.DELIVERY_READY)


def test_runtime_transition_returns_a_new_snapshot() -> None:
    before = _run(RunStatus.CREATED)
    after = transition(before, RunStatus.INGESTING, cause=_cause(), guard_payload=None)
    assert before.status is RunStatus.CREATED
    assert after.status is RunStatus.INGESTING


def test_agent_cannot_cause_any_transition() -> None:
    cause = _cause().model_copy(update={"actor": "AGENT"})
    with pytest.raises(InvalidStateTransition, match="AGENT"):
        transition(_run(RunStatus.CREATED), RunStatus.INGESTING, cause=cause, guard_payload=None)


def test_created_cannot_skip_to_delivery_ready() -> None:
    with pytest.raises(InvalidStateTransition, match="CREATED -> DELIVERY_READY"):
        transition(_run(RunStatus.CREATED), RunStatus.DELIVERY_READY, cause=_cause(), guard_payload=None)


@pytest.mark.parametrize("terminal", tuple(TERMINAL_STATUSES))
def test_terminal_states_reject_every_transition(terminal: RunStatus) -> None:
    for target in RunStatus:
        with pytest.raises(InvalidStateTransition):
            transition(_run(terminal), target, cause=_cause(), guard_payload=None)


def test_findings_terminal_edge_requires_same_run_cycle_verdict() -> None:
    run = _run(RunStatus.FINDINGS_READY, cycle=1)
    verdict = compute(
        TerminalInput(run_id=run.run_id, cycle=run.cycle, originals_integrity_verified=True)
    )
    assert verdict is not None and verdict.outcome is TerminalOutcome.DELIVERY_READY
    assert transition(
        run,
        RunStatus.DELIVERY_READY,
        cause=_cause("TERMINAL_COMPUTED"),
        guard_payload=verdict,
    ).status is RunStatus.DELIVERY_READY
    with pytest.raises(InvalidStateTransition, match="terminal verdict"):
        transition(
            run,
            RunStatus.DELIVERY_READY,
            cause=_cause(),
            guard_payload=verdict.model_copy(update={"cycle": 2}),
        )


def test_requirements_block_requires_typed_no_executable_guard() -> None:
    run = _run(RunStatus.REQUIREMENTS_ADMITTED)
    with pytest.raises(InvalidStateTransition, match="NoExecutableRequirements"):
        transition(run, RunStatus.BLOCKED, cause=_cause(), guard_payload=None)
    result = transition(
        run,
        RunStatus.BLOCKED,
        cause=_cause(),
        guard_payload=NoExecutableRequirements(run_id=run.run_id),
    )
    assert result.status is RunStatus.BLOCKED


def test_tier1_remediation_requires_exact_open_option_binding() -> None:
    binding = OptionBinding(
        run_id="run_lifecycle_001",
        finding_id="finding_001",
        option_id="option_001",
        args_hash="a" * 64,
        tier=1,
    )
    run = _run(RunStatus.FINDINGS_READY).model_copy(update={"admissible_options": (binding,)})
    selection = RemediationSelection(**binding.model_dump())
    assert transition(
        run,
        RunStatus.REMEDIATING,
        cause=_cause(),
        guard_payload=selection,
    ).status is RunStatus.REMEDIATING
    with pytest.raises(InvalidStateTransition, match="Tier-1 option"):
        transition(
            run,
            RunStatus.REMEDIATING,
            cause=_cause(),
            guard_payload=selection.model_copy(update={"args_hash": "b" * 64}),
        )


def test_tier2_resume_requires_issued_exact_authorization() -> None:
    binding = OptionBinding(
        run_id="run_lifecycle_001",
        finding_id="finding_001",
        option_id="option_001",
        args_hash="a" * 64,
        tier=2,
    )
    run = _run(RunStatus.AWAITING_HUMAN_DECISION).model_copy(
        update={"pending_decision_id": "decision_001", "pending_option": binding}
    )
    authorization = AuthorizationBinding(
        authorization_id="auth_opaque-001",
        run_id=run.run_id,
        decision_id="decision_001",
        option_id="option_001",
        args_hash="a" * 64,
        status=AuthorizationStatus.ISSUED,
    )
    assert transition(
        run,
        RunStatus.REMEDIATING,
        cause=_cause(),
        guard_payload=authorization,
    ).status is RunStatus.REMEDIATING
    with pytest.raises(InvalidStateTransition, match="authorization"):
        transition(
            run,
            RunStatus.REMEDIATING,
            cause=_cause(),
            guard_payload=authorization.model_copy(update={"option_id": "option_other"}),
        )


def test_entering_human_wait_requires_a_typed_pending_decision() -> None:
    run = _run(RunStatus.FINDINGS_READY)
    binding = OptionBinding(
        run_id=run.run_id,
        finding_id="finding_001",
        option_id="option_001",
        args_hash="a" * 64,
        tier=2,
    )
    pending = DecisionPending(
        run_id=run.run_id,
        decision_id="decision_001",
        option=binding,
    )

    waiting = transition(
        run,
        RunStatus.AWAITING_HUMAN_DECISION,
        cause=_cause(),
        guard_payload=pending,
    )
    assert waiting.pending_decision_id == "decision_001"
    assert waiting.pending_option == binding
    with pytest.raises(InvalidStateTransition, match="DecisionPending"):
        transition(
            run,
            RunStatus.AWAITING_HUMAN_DECISION,
            cause=_cause(),
            guard_payload=None,
        )


@pytest.mark.parametrize(
    ("remaining_options", "target"),
    [(True, RunStatus.FINDINGS_READY), (False, RunStatus.BLOCKED)],
)
def test_denied_decision_routes_by_remaining_options(remaining_options: bool, target: RunStatus) -> None:
    run = _run(RunStatus.AWAITING_HUMAN_DECISION).model_copy(
        update={"pending_decision_id": "decision_001"}
    )
    resolution = DecisionResolution(
        run_id=run.run_id,
        decision_id="decision_001",
        choice=DecisionChoice.DENIED,
        admissible_options_remain=remaining_options,
    )
    assert transition(run, target, cause=_cause(), guard_payload=resolution).status is target


def test_remediation_cycle_limit_rejects_reentry() -> None:
    run = _run(RunStatus.REMEDIATING, cycle=3)
    with pytest.raises(InvalidStateTransition, match="cycle limit"):
        transition(
            run,
            RunStatus.INSPECTING,
            cause=_cause(),
            guard_payload=RemediationCompleted(run_id=run.run_id, next_cycle=4),
        )


def test_completed_remediation_increments_cycle_before_reinspection() -> None:
    run = _run(RunStatus.REMEDIATING, cycle=1)
    after = transition(
        run,
        RunStatus.INSPECTING,
        cause=_cause(),
        guard_payload=RemediationCompleted(run_id=run.run_id, next_cycle=2),
    )
    assert after.status is RunStatus.INSPECTING
    assert after.cycle == 2
