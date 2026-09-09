"""M4 evidence-safe remediation transaction and recovery tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from aircheck.authority import (
    AuthorityError,
    AuthorityRunContext,
    InMemoryAuthorityStore,
    PendingFinding,
    approve_decision,
    create_decision_request,
)
from aircheck.domain.actions import RemediationOption
from aircheck.domain.assets import Asset
from aircheck.domain.events import ActionCompletedPayload, ActionFailedPayload, ActionStartedPayload
from aircheck.domain.state_machine import RunStateSnapshot
from aircheck.domain.status import RunStatus
from aircheck.domain.types import (
    AssetProtection,
    AssetRelation,
    AssetRole,
    AuthorizationStatus,
    FindingLifecycle,
)
from aircheck.persistence import LocalDurableStore
from aircheck.runtime.models import RuntimeAsset, RuntimeFinding, RuntimeRun
from aircheck.runtime.orchestrator import HeadlessOrchestrator
from aircheck.runtime.remediation import (
    InjectedActionCrash,
    RemediationExecutor,
)
from aircheck.tools import TOOL_REGISTRY


RUN_ID = "run_m4_remediation"


def _asset(
    asset_id: str,
    protection: AssetProtection,
    filename: str,
    sha256: str,
    relative_path: str,
    *,
    predecessor: str | None = None,
    relation: AssetRelation | None = None,
) -> RuntimeAsset:
    return RuntimeAsset(
        asset=Asset(
            asset_id=asset_id,
            run_id=RUN_ID,
            role=AssetRole.PROGRAM_MASTER,
            filename=filename,
            sha256=sha256,
            size_bytes=6,
            protection=protection,
            predecessor=predecessor,
            relation=relation,
        ),
        relative_path=relative_path,
    )


def _fixture(tmp_path: Path):
    from hashlib import sha256

    workspace = tmp_path / "workspace"
    original_path = workspace / "originals" / "asset_original_master" / "bad.mov"
    working_path = workspace / "working" / "asset_working_master" / "bad.mov"
    original_path.parent.mkdir(parents=True)
    working_path.parent.mkdir(parents=True)
    original_path.write_bytes(b"master")
    working_path.write_bytes(b"master")
    digest = sha256(b"master").hexdigest()
    original = _asset(
        "asset_original_master",
        AssetProtection.ORIGINAL,
        "bad.mov",
        digest,
        "originals/asset_original_master/bad.mov",
    )
    working = _asset(
        "asset_working_master",
        AssetProtection.WORKING,
        "bad.mov",
        digest,
        "working/asset_working_master/bad.mov",
        predecessor=original.asset.asset_id,
        relation=AssetRelation.COPIED_FROM,
    )
    payload = TOOL_REGISTRY["rename_delivery_copy"].input_model(
        asset_id=working.asset.asset_id,
        option_id="option_rename_master",
        new_filename="GOOD.mov",
    )
    option = RemediationOption.from_call(
        option_id=payload.option_id,
        finding_id="finding_filename",
        tool="rename_delivery_copy",
        payload=payload,
        tier=1,
        description="Create a correctly named working successor.",
        produces_derivative=False,
    )
    finding = RuntimeFinding(
        finding_id=option.finding_id,
        requirement_id="req_filename",
        asset_id=working.asset.asset_id,
        cycle_opened=0,
        status=FindingLifecycle.OPEN,
        options=(option,),
        observed_rendered="bad.mov",
        expected_rendered="GOOD.mov",
    )
    record = RuntimeRun(
        run_id=RUN_ID,
        profile_id="northstar_broadcast_master_v1",
        content_type="program",
        assets=(original, working),
        findings=(finding,),
        original_hashes=((original.asset.asset_id, digest),),
    )
    authority = AuthorityRunContext(
        run_id=RUN_ID,
        status=RunStatus.FINDINGS_READY,
        assets=(working.asset,),
        findings=(
            PendingFinding(
                finding_id=finding.finding_id,
                status=FindingLifecycle.OPEN,
            ),
        ),
        options=(option,),
    )
    state = RunStateSnapshot(
        run_id=RUN_ID,
        status=RunStatus.REMEDIATING,
        cycle=0,
    )
    store = LocalDurableStore(tmp_path / "store")
    store.save_snapshot(state)
    store.save_runtime_run(record)
    return workspace, store, record, state, authority, option, payload


def test_success_persists_successor_before_completed_event_and_preserves_original(
    tmp_path: Path,
) -> None:
    workspace, store, record, state, authority, option, payload = _fixture(tmp_path)
    original_bytes = (
        workspace / "originals" / "asset_original_master" / "bad.mov"
    ).read_bytes()

    updated, result = RemediationExecutor(
        workspace=workspace,
        store=store,
        authority_store=InMemoryAuthorityStore(),
    ).execute(
        run=record,
        state=state,
        authority_context=authority,
        option=option,
        payload=payload,
        actor="SYSTEM",
    )

    assert result.status.value == "OK"
    successor = next(item for item in updated.assets if item.asset.asset_id == result.new_asset_id)
    assert successor.asset.predecessor == "asset_working_master"
    assert successor.asset.relation is AssetRelation.RENAMED_FROM
    assert (workspace / successor.relative_path).read_bytes() == b"master"
    assert (
        workspace / "originals" / "asset_original_master" / "bad.mov"
    ).read_bytes() == original_bytes
    events = store.list_events(RUN_ID)
    assert isinstance(events[-2].payload, ActionStartedPayload)
    assert isinstance(events[-1].payload, ActionCompletedPayload)
    assert store.load_runtime_run(RUN_ID).assets == updated.assets


def test_restart_reconciles_completed_action_count_from_the_ledger(
    tmp_path: Path,
) -> None:
    workspace, store, record, state, authority, option, payload = _fixture(tmp_path)
    updated, result = RemediationExecutor(
        workspace=workspace,
        store=store,
        authority_store=InMemoryAuthorityStore(),
    ).execute(
        run=record,
        state=state,
        authority_context=authority,
        option=option,
        payload=payload,
        actor="SYSTEM",
    )
    assert result.status.value == "OK"
    assert updated.autonomous_remediations == 0

    HeadlessOrchestrator(
        workspace=workspace,
        store_root=tmp_path / "store",
        run_id=RUN_ID,
    ).advance(object())

    recovered = store.load_runtime_run(RUN_ID)
    assert recovered.autonomous_remediations == 1
    assert recovered.authorized_remediations == 0


def test_crash_before_promotion_is_closed_failed_without_current_output(
    tmp_path: Path,
) -> None:
    workspace, store, record, state, authority, option, payload = _fixture(tmp_path)
    executor = RemediationExecutor(
        workspace=workspace,
        store=store,
        authority_store=InMemoryAuthorityStore(),
    )

    with pytest.raises(InjectedActionCrash, match="after_temp"):
        executor.execute(
            run=record,
            state=state,
            authority_context=authority,
            option=option,
            payload=payload,
            actor="SYSTEM",
            crash_at="after_temp",
        )

    recovered = executor.recover(run=record, state=state)
    assert recovered.assets == record.assets
    assert not tuple((workspace / "working").rglob("GOOD.mov"))
    assert not tuple((workspace / ".actions").glob("*.tmp*"))
    assert isinstance(store.list_events(RUN_ID)[-1].payload, ActionFailedPayload)
    assert executor.recover(run=recovered, state=state) == recovered


def test_promoted_but_uncommitted_output_is_committed_once_by_recovery(
    tmp_path: Path,
) -> None:
    workspace, store, record, state, authority, option, payload = _fixture(tmp_path)
    executor = RemediationExecutor(
        workspace=workspace,
        store=store,
        authority_store=InMemoryAuthorityStore(),
    )

    with pytest.raises(InjectedActionCrash, match="after_promote"):
        executor.execute(
            run=record,
            state=state,
            authority_context=authority,
            option=option,
            payload=payload,
            actor="SYSTEM",
            crash_at="after_promote",
        )

    recovered = executor.recover(run=record, state=state)
    completed = tuple(
        event for event in store.list_events(RUN_ID)
        if isinstance(event.payload, ActionCompletedPayload)
    )
    assert len(completed) == 1
    assert len(recovered.assets) == len(record.assets) + 1
    assert (workspace / recovered.assets[-1].relative_path).read_bytes() == b"master"

    again = executor.recover(run=recovered, state=state)
    assert again == recovered
    assert len(tuple(
        event for event in store.list_events(RUN_ID)
        if isinstance(event.payload, ActionCompletedPayload)
    )) == 1


def test_headless_restart_reconciles_promoted_action_before_reinspection(
    tmp_path: Path,
) -> None:
    workspace, store, record, state, authority, option, payload = _fixture(tmp_path)
    executor = RemediationExecutor(
        workspace=workspace,
        store=store,
        authority_store=InMemoryAuthorityStore(),
    )
    with pytest.raises(InjectedActionCrash, match="after_promote"):
        executor.execute(
            run=record,
            state=state,
            authority_context=authority,
            option=option,
            payload=payload,
            actor="SYSTEM",
            crash_at="after_promote",
        )

    resumed = HeadlessOrchestrator(
        workspace=workspace,
        store_root=tmp_path / "store",
        run_id=RUN_ID,
    ).advance(object())  # Recovery does not invoke the semantic boundary.

    assert resumed.status is RunStatus.FINDINGS_READY
    assert resumed.cycle == 1
    recovered = store.load_runtime_run(RUN_ID)
    assert len(recovered.assets) == len(record.assets) + 1
    assert len(tuple(
        event for event in store.list_events(RUN_ID)
        if isinstance(event.payload, ActionCompletedPayload)
    )) == 1


def test_post_promotion_store_failure_quarantines_uncommitted_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, store, record, state, authority, option, payload = _fixture(tmp_path)
    original_save = store.save_runtime_run

    def fail_successor_commit(value):
        if len(value.assets) > len(record.assets):
            raise OSError("injected store commit failure")
        original_save(value)

    monkeypatch.setattr(store, "save_runtime_run", fail_successor_commit)
    updated, result = RemediationExecutor(
        workspace=workspace,
        store=store,
        authority_store=InMemoryAuthorityStore(),
    ).execute(
        run=record,
        state=state,
        authority_context=authority,
        option=option,
        payload=payload,
        actor="SYSTEM",
    )

    assert updated == record
    assert result.status.value == "ERROR"
    assert store.load_runtime_run(RUN_ID) == record
    assert not tuple(workspace.rglob("GOOD.mov"))
    assert len(tuple(workspace.rglob("*.quarantine"))) == 1
    assert isinstance(store.list_events(RUN_ID)[-1].payload, ActionFailedPayload)


def test_recovery_refuses_corrupt_persisted_successor_instead_of_false_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, store, record, state, authority, option, payload = _fixture(tmp_path)
    executor = RemediationExecutor(
        workspace=workspace,
        store=store,
        authority_store=InMemoryAuthorityStore(),
    )

    def interrupt_completion(*args, **kwargs):
        raise OSError("injected interruption after runtime commit")

    monkeypatch.setattr(executor, "_append_completed", interrupt_completion)
    with pytest.raises(OSError, match="runtime commit"):
        executor.execute(
            run=record,
            state=state,
            authority_context=authority,
            option=option,
            payload=payload,
            actor="SYSTEM",
        )
    persisted = store.load_runtime_run(RUN_ID)
    successor = next(
        item for item in persisted.assets if item.asset.created_by_action is not None
    )
    (workspace / successor.relative_path).write_bytes(b"corrupt")

    failed = HeadlessOrchestrator(
        workspace=workspace,
        store_root=tmp_path / "store",
        run_id=RUN_ID,
    ).advance(object())

    events = store.list_events(RUN_ID)
    assert failed.status is RunStatus.FAILED
    assert any(isinstance(event.payload, ActionFailedPayload) for event in events)
    assert not any(isinstance(event.payload, ActionCompletedPayload) for event in events)
    assert not (workspace / successor.relative_path).exists()
    assert len(tuple(workspace.rglob("*.quarantine"))) == 1


def test_failed_tier_two_attempt_voids_authorization_and_replay_starts_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, store, record, state, _, _, _ = _fixture(tmp_path)
    working = next(
        item for item in record.assets if item.asset.protection is AssetProtection.WORKING
    )
    placeholder = TOOL_REGISTRY["create_normalized_audio_derivative"].input_model(
        asset_id=working.asset.asset_id,
        option_id="option_tier2_failure",
        target_lufs=-24,
        tolerance=2,
        authorization_id="auth_placeholder",
    )
    option = RemediationOption.from_call(
        option_id=placeholder.option_id,
        finding_id="finding_tier2_failure",
        tool="create_normalized_audio_derivative",
        payload=placeholder,
        tier=2,
        description="Create one authorized derivative.",
        produces_derivative=True,
    )
    finding = RuntimeFinding(
        finding_id=option.finding_id,
        requirement_id="req_loudness",
        asset_id=working.asset.asset_id,
        cycle_opened=0,
        status=FindingLifecycle.OPEN,
        options=(option,),
        observed_rendered="-19 LUFS",
        expected_rendered="-26 to -22 LUFS",
    )
    record = record.model_copy(update={"findings": (finding,)})
    store.save_runtime_run(record)
    authority = AuthorityRunContext(
        run_id=RUN_ID,
        status=RunStatus.FINDINGS_READY,
        assets=(working.asset,),
        findings=(PendingFinding(finding_id=finding.finding_id, status=finding.status),),
        options=(option,),
    )
    request = create_decision_request(
        authority,
        option,
        now=datetime(2026, 9, 8, tzinfo=UTC),
    )
    authorization = approve_decision(
        request,
        option,
        actor="human:mu",
        store=store,
    ).authorization
    assert authorization is not None
    payload = placeholder.model_copy(
        update={"authorization_id": authorization.authorization_id}
    )
    executor = RemediationExecutor(
        workspace=workspace,
        store=store,
        authority_store=store,
    )

    invocation = {}

    def fail_render(*args, **kwargs):
        invocation.update(kwargs)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("aircheck.runtime.remediation.subprocess.run", fail_render)
    _, result = executor.execute(
        run=record,
        state=state,
        authority_context=authority,
        option=option,
        payload=payload,
        actor="SYSTEM",
    )
    assert result.status.value == "ERROR"
    assert invocation["timeout"] == 120
    assert store.get(authorization.authorization_id).status is AuthorizationStatus.VOID
    starts_before = sum(
        isinstance(event.payload, ActionStartedPayload)
        for event in store.list_events(RUN_ID)
    )

    with pytest.raises(AuthorityError, match="ISSUED"):
        executor.execute(
            run=record,
            state=state,
            authority_context=authority,
            option=option,
            payload=payload,
            actor="SYSTEM",
        )
    assert sum(
        isinstance(event.payload, ActionStartedPayload)
        for event in store.list_events(RUN_ID)
    ) == starts_before
