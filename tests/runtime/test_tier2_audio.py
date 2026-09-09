"""M4 authorized Tier-2 audio derivative and replay defenses."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aircheck.authority import (
    AuthorityError,
    AuthorityRunContext,
    PendingFinding,
    approve_decision,
    create_decision_request,
)
from aircheck.domain.actions import RemediationOption
from aircheck.domain.assets import Asset
from aircheck.domain.events import ActionStartedPayload
from aircheck.domain.state_machine import RunStateSnapshot
from aircheck.domain.status import RunStatus
from aircheck.domain.types import (
    AssetProtection,
    AssetRelation,
    AssetRole,
    AuthorizationStatus,
    FindingLifecycle,
)
from aircheck.media import InspectionContext, execute_inspection, hash_file
from aircheck.persistence import LocalDurableStore
from aircheck.runtime.models import RuntimeAsset, RuntimeFinding, RuntimeRun
from aircheck.runtime.remediation import RemediationExecutor
from aircheck.tools import TOOL_REGISTRY
from aircheck.tools.contracts import MeasureLoudnessInput
from aircheck.tools.paths import RuntimeAssetPath, WorkspaceResolver
from synthetic.generator.last_lightkeeper import build_universe


RUN_ID = "run_m4_tier2_audio"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="M4 media proof requires ffmpeg and ffprobe",
)
def test_authorized_audio_derivative_reverifies_and_replay_has_no_side_effect(
    tmp_path: Path,
) -> None:
    universe = build_universe(tmp_path / "universe")
    source = universe.hero.master
    workspace = tmp_path / "workspace"
    original_path = workspace / "originals" / "asset_original_master" / source.name
    working_path = workspace / "working" / "asset_working_master" / source.name
    original_path.parent.mkdir(parents=True)
    working_path.parent.mkdir(parents=True)
    shutil.copyfile(source, original_path)
    shutil.copyfile(source, working_path)
    source_hash = hash_file(source)
    original = RuntimeAsset(
        asset=Asset(
            asset_id="asset_original_master",
            run_id=RUN_ID,
            role=AssetRole.PROGRAM_MASTER,
            filename=source.name,
            sha256=source_hash,
            size_bytes=source.stat().st_size,
            protection=AssetProtection.ORIGINAL,
        ),
        relative_path=f"originals/asset_original_master/{source.name}",
    )
    working = RuntimeAsset(
        asset=Asset(
            asset_id="asset_working_master",
            run_id=RUN_ID,
            role=AssetRole.PROGRAM_MASTER,
            filename=source.name,
            sha256=source_hash,
            size_bytes=source.stat().st_size,
            protection=AssetProtection.WORKING,
            predecessor=original.asset.asset_id,
            relation=AssetRelation.COPIED_FROM,
        ),
        relative_path=f"working/asset_working_master/{source.name}",
    )
    unissued_payload = TOOL_REGISTRY[
        "create_normalized_audio_derivative"
    ].input_model(
        asset_id=working.asset.asset_id,
        option_id="option_normalize_audio",
        target_lufs=-24,
        tolerance=2,
        authorization_id="auth_placeholder",
    )
    option = RemediationOption.from_call(
        option_id=unissued_payload.option_id,
        finding_id="finding_loudness",
        tool="create_normalized_audio_derivative",
        payload=unissued_payload,
        tier=2,
        description="Create a -24 LUFS delivery derivative.",
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
    record = RuntimeRun(
        run_id=RUN_ID,
        profile_id="northstar_broadcast_master_v1",
        content_type="program",
        assets=(original, working),
        findings=(finding,),
        original_hashes=((original.asset.asset_id, source_hash),),
    )
    authority_context = AuthorityRunContext(
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
    store = LocalDurableStore(tmp_path / "store")
    request = create_decision_request(
        authority_context,
        option,
        now=datetime(2026, 9, 7, 22, 0, tzinfo=UTC),
    )
    authorization = approve_decision(
        request,
        option,
        actor="human:mu",
        store=store,
    ).authorization
    assert authorization is not None
    payload = unissued_payload.model_copy(
        update={"authorization_id": authorization.authorization_id}
    )
    state = RunStateSnapshot(
        run_id=RUN_ID,
        status=RunStatus.REMEDIATING,
        cycle=1,
    )
    store.save_snapshot(state)
    store.save_runtime_run(record)
    executor = RemediationExecutor(
        workspace=workspace,
        store=store,
        authority_store=store,
    )

    updated, result = executor.execute(
        run=record,
        state=state,
        authority_context=authority_context,
        option=option,
        payload=payload,
        actor="SYSTEM",
    )

    assert result.status.value == "OK"
    assert store.get(authorization.authorization_id).status is AuthorizationStatus.CONSUMED
    derivative = next(
        item for item in updated.assets if item.asset.asset_id == result.new_asset_id
    )
    assert derivative.asset.protection is AssetProtection.DERIVATIVE
    assert derivative.asset.relation is AssetRelation.DERIVED_FROM
    assert derivative.asset.predecessor == working.asset.asset_id
    assert derivative.asset.filename == working.asset.filename
    assert hash_file(original_path) == source_hash

    current = updated.current_assets()
    inspection = execute_inspection(
        "measure_loudness",
        MeasureLoudnessInput(
            asset_id=derivative.asset.asset_id,
            stream="primary_audio",
        ),
        InspectionContext(
            run_id=RUN_ID,
            resolver=WorkspaceResolver(
                workspace,
                tuple(
                    RuntimeAssetPath(asset=item.asset, relative_path=item.relative_path)
                    for item in current
                ),
            ),
            assets=tuple(item.asset for item in current),
        ),
    )
    integrated = next(
        value.value for value in inspection.measurements
        if value.measurement_key == "audio.integrated_loudness"
    )
    assert isinstance(integrated, float)
    assert -26 <= integrated <= -22

    starts_before = sum(
        isinstance(event.payload, ActionStartedPayload)
        for event in store.list_events(RUN_ID)
    )
    files_before = tuple(sorted(path.as_posix() for path in workspace.rglob("*") if path.is_file()))
    with pytest.raises(AuthorityError, match="ISSUED"):
        executor.execute(
            run=record,
            state=state,
            authority_context=authority_context,
            option=option,
            payload=payload,
            actor="SYSTEM",
        )
    assert sum(
        isinstance(event.payload, ActionStartedPayload)
        for event in store.list_events(RUN_ID)
    ) == starts_before
    assert tuple(sorted(path.as_posix() for path in workspace.rglob("*") if path.is_file())) == files_before
