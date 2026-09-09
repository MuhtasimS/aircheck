"""R2.9 durable-store contract and real subprocess restart tests."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aircheck.authority import (
    AuthorityRunContext,
    PendingFinding,
    approve_decision,
    create_decision_request,
)
from aircheck.domain.actions import Action, RemediationOption
from aircheck.domain.assets import Asset
from aircheck.domain.events import (
    ActionCompletedPayload,
    ActionStartedPayload,
    LedgerEvent,
    LedgerEventType,
    StateTransitionPayload,
    event_hash,
)
from aircheck.domain.state_machine import OptionBinding, RunStateSnapshot
from aircheck.domain.status import RunStatus
from aircheck.domain.types import (
    AssetProtection,
    AssetRole,
    ActionActor,
    AuthorizationStatus,
    FindingLifecycle,
)
from aircheck.persistence import LocalDurableStore, StoreConsistencyError
from aircheck.tools import TOOL_REGISTRY


ROOT = Path(__file__).resolve().parents[2]


def _option() -> RemediationOption:
    payload = TOOL_REGISTRY["create_normalized_audio_derivative"].input_model(
        asset_id="asset_working",
        option_id="option_audio",
        target_lufs=-24,
        tolerance=2,
        authorization_id="auth_placeholder",
    )
    return RemediationOption.from_call(
        option_id="option_audio",
        finding_id="finding_001",
        tool="create_normalized_audio_derivative",
        payload=payload,
        tier=2,
        description="Normalize a delivery derivative.",
        produces_derivative=True,
    )


def _authority_context(option: RemediationOption) -> AuthorityRunContext:
    asset = Asset(
        asset_id="asset_working",
        run_id="run_store_001",
        role=AssetRole.PROGRAM_MASTER,
        filename="master.mov",
        sha256="a" * 64,
        size_bytes=100,
        protection=AssetProtection.WORKING,
    )
    return AuthorityRunContext(
        run_id="run_store_001",
        status=RunStatus.FINDINGS_READY,
        assets=(asset,),
        findings=(
            PendingFinding(
                finding_id="finding_001",
                status=FindingLifecycle.OPEN,
            ),
        ),
        options=(option,),
    )


def _event(
    *,
    seq: int,
    event_type: LedgerEventType,
    payload,
    prev_hash: str | None = None,
) -> LedgerEvent:
    return LedgerEvent(
        event_id=f"evt_{seq:03d}",
        run_id="run_store_001",
        seq=seq,
        timestamp=datetime(2026, 9, 4, 12, seq, tzinfo=UTC),
        type=event_type,
        actor="SYSTEM",
        summary=f"Event {seq}",
        payload=payload,
        refs=(),
        prev_hash=prev_hash,
    )


def test_snapshot_event_consistency_and_append_only_sequence(tmp_path: Path) -> None:
    store = LocalDurableStore(tmp_path)
    transition = _event(
        seq=1,
        event_type=LedgerEventType.STATE_TRANSITION,
        payload=StateTransitionPayload(
            from_state=RunStatus.FINDINGS_READY,
            to_state=RunStatus.AWAITING_HUMAN_DECISION,
        ),
    )
    store.append_event(transition)

    snapshot = RunStateSnapshot(
        run_id="run_store_001",
        status=RunStatus.AWAITING_HUMAN_DECISION,
        pending_decision_id="decision_001",
    )
    store.save_snapshot(snapshot, require_pending_decision=False)
    assert store.load_snapshot(snapshot.run_id) == snapshot
    assert store.list_events(snapshot.run_id) == (transition,)

    bad_sequence = _event(
        seq=3,
        event_type=LedgerEventType.ACTION_STARTED,
        payload=ActionStartedPayload(
            action_id="action_001",
            tool="rename_delivery_copy",
            authorization_id=None,
        ),
        prev_hash=event_hash(transition),
    )
    with pytest.raises(StoreConsistencyError, match="sequence"):
        store.append_event(bad_sequence)

    with pytest.raises(StoreConsistencyError, match="snapshot status"):
        store.save_snapshot(
            snapshot.model_copy(update={"status": RunStatus.REMEDIATING}),
            require_pending_decision=False,
        )


def test_action_completed_requires_a_preceding_start(tmp_path: Path) -> None:
    store = LocalDurableStore(tmp_path)
    completed = _event(
        seq=1,
        event_type=LedgerEventType.ACTION_COMPLETED,
        payload=ActionCompletedPayload(
            action_id="action_001",
            new_asset_id="asset_successor",
            sha256="b" * 64,
        ),
    )
    with pytest.raises(StoreConsistencyError, match="ACTION_STARTED"):
        store.append_event(completed)


def test_recovery_can_identify_orphaned_action_starts(tmp_path: Path) -> None:
    store = LocalDurableStore(tmp_path)
    started = _event(
        seq=1,
        event_type=LedgerEventType.ACTION_STARTED,
        payload=ActionStartedPayload(
            action_id="action_001",
            tool="rename_delivery_copy",
            authorization_id=None,
        ),
    )
    store.append_event(started)
    assert store.unfinished_actions("run_store_001") == ("action_001",)

    completed = _event(
        seq=2,
        event_type=LedgerEventType.ACTION_COMPLETED,
        payload=ActionCompletedPayload(
            action_id="action_001",
            new_asset_id="asset_successor",
            sha256="b" * 64,
        ),
        prev_hash=event_hash(started),
    )
    store.append_event(completed)
    assert store.unfinished_actions("run_store_001") == ()


def test_pending_decision_and_authorization_survive_real_process_restart(
    tmp_path: Path,
) -> None:
    store = LocalDurableStore(tmp_path)
    option = _option()
    authority_context = _authority_context(option)
    request = create_decision_request(
        authority_context,
        option,
        now=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    store.save_decision_request(request)
    approval = approve_decision(request, option, actor="human:mu", store=store)
    authorization = approval.authorization
    assert authorization is not None

    pending_binding = OptionBinding(
        run_id=authority_context.run_id,
        finding_id=option.finding_id,
        option_id=option.option_id,
        args_hash=option.args_hash,
        tier=2,
    )
    transition = _event(
        seq=1,
        event_type=LedgerEventType.STATE_TRANSITION,
        payload=StateTransitionPayload(
            from_state=RunStatus.FINDINGS_READY,
            to_state=RunStatus.AWAITING_HUMAN_DECISION,
        ),
    )
    store.append_event(transition)
    store.save_snapshot(
        RunStateSnapshot(
            run_id=authority_context.run_id,
            status=RunStatus.AWAITING_HUMAN_DECISION,
            pending_decision_id=request.decision_id,
            pending_option=pending_binding,
        )
    )

    code = """
from aircheck.persistence import LocalDurableStore
store = LocalDurableStore(ROOT)
snapshot = store.load_snapshot('run_store_001')
decision = store.load_pending_decision('run_store_001')
authorization = store.get(AUTH_ID)
assert snapshot.pending_decision_id == decision.decision_id
assert authorization.status.value == 'ISSUED'
store.consume(
    AUTH_ID,
    run_id='run_store_001',
    option_id='option_audio',
    args_hash=ARGS_HASH,
)
print(snapshot.status.value, decision.status.value, authorization.status.value)
"""
    code = code.replace("ROOT", repr(str(tmp_path)))
    code = code.replace("AUTH_ID", repr(authorization.authorization_id))
    code = code.replace("ARGS_HASH", repr(option.args_hash))
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={
            **__import__("os").environ,
            "PYTHONUTF8": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "AWAITING_HUMAN_DECISION PENDING ISSUED"
    assert store.get(authorization.authorization_id).status is AuthorizationStatus.CONSUMED


def test_authorization_consume_and_action_start_recover_as_one_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalDurableStore(tmp_path)
    option = _option()
    request = create_decision_request(
        _authority_context(option),
        option,
        now=datetime(2026, 9, 4, 13, 0, tzinfo=UTC),
    )
    authorization = approve_decision(
        request,
        option,
        actor="human:mu",
        store=store,
    ).authorization
    assert authorization is not None
    action = Action(
        action_id="action_authorized_wal",
        run_id="run_store_001",
        cycle=1,
        tool=option.tool,
        option_id=option.option_id,
        args_hash=option.args_hash,
        authorization_id=authorization.authorization_id,
        actor=ActionActor.SYSTEM,
    )

    def interrupt_start(_: Action) -> None:
        raise OSError("injected interruption after capability consumption")

    monkeypatch.setattr(store, "append_action_started", interrupt_start)
    with pytest.raises(OSError, match="injected interruption"):
        store.begin_authorized_action(action)
    assert store.get(authorization.authorization_id).status is AuthorizationStatus.CONSUMED

    recovered = LocalDurableStore(tmp_path)
    starts = tuple(
        event
        for event in recovered.list_events("run_store_001")
        if isinstance(event.payload, ActionStartedPayload)
    )
    assert len(starts) == 1
    assert starts[0].payload.action_id == action.action_id
    assert recovered.get(authorization.authorization_id).status is AuthorizationStatus.CONSUMED
    assert not tuple((tmp_path / "transactions" / "actions").glob("*.json"))


def test_transition_event_and_snapshot_reconcile_after_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalDurableStore(tmp_path)
    created = RunStateSnapshot(run_id="run_store_001", status=RunStatus.CREATED)
    store.save_snapshot(created)
    event = _event(
        seq=1,
        event_type=LedgerEventType.STATE_TRANSITION,
        payload=StateTransitionPayload(
            from_state=RunStatus.CREATED,
            to_state=RunStatus.INGESTING,
        ),
    )
    ingesting = created.model_copy(update={"status": RunStatus.INGESTING})
    original_save = store.save_snapshot

    def interrupt_snapshot(*args, **kwargs):
        raise OSError("injected interruption after transition event")

    monkeypatch.setattr(store, "save_snapshot", interrupt_snapshot)
    with pytest.raises(OSError, match="injected interruption"):
        store.commit_transition(ingesting, event)
    assert store.list_events("run_store_001") == (event,)
    monkeypatch.setattr(store, "save_snapshot", original_save)

    recovered = LocalDurableStore(tmp_path)
    assert recovered.load_snapshot("run_store_001") == ingesting
    assert recovered.list_events("run_store_001") == (event,)
    assert not tuple((tmp_path / "transactions" / "transitions").glob("*.json"))
