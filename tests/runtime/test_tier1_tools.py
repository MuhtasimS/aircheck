"""M4 real Tier-1 remediation behavior on append-only working successors."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from aircheck.authority import AuthorityRunContext, InMemoryAuthorityStore, PendingFinding
from aircheck.domain.actions import RemediationOption
from aircheck.domain.assets import Asset
from aircheck.domain.state_machine import RunStateSnapshot
from aircheck.domain.status import RunStatus
from aircheck.domain.types import (
    AssetProtection,
    AssetRelation,
    AssetRole,
    FindingLifecycle,
)
from aircheck.media import InspectionContext, execute_inspection
from aircheck.persistence import LocalDurableStore
from aircheck.runtime.models import RuntimeAsset, RuntimeFinding, RuntimeRun
from aircheck.runtime.remediation import RemediationExecutor
from aircheck.tools import TOOL_REGISTRY
from aircheck.tools.contracts import InspectCaptionsInput, VerifyManifestInput
from aircheck.tools.paths import RuntimeAssetPath, WorkspaceResolver


RUN_ID = "run_m4_tier1"


def _stored_asset(
    *,
    asset_id: str,
    role: AssetRole,
    filename: str,
    content: bytes,
    protection: AssetProtection,
    relative_path: str,
    predecessor: str | None = None,
) -> RuntimeAsset:
    relation = AssetRelation.COPIED_FROM if predecessor else None
    return RuntimeAsset(
        asset=Asset(
            asset_id=asset_id,
            run_id=RUN_ID,
            role=role,
            filename=filename,
            sha256=sha256(content).hexdigest(),
            size_bytes=len(content),
            protection=protection,
            predecessor=predecessor,
            relation=relation,
        ),
        relative_path=relative_path,
    )


def _context(record: RuntimeRun) -> InspectionContext:
    current = record.current_assets()
    return InspectionContext(
        run_id=record.run_id,
        resolver=WorkspaceResolver(
            _context.workspace,
            tuple(
                RuntimeAssetPath(asset=item.asset, relative_path=item.relative_path)
                for item in current
            ),
        ),
        assets=tuple(item.asset for item in current),
    )


def test_caption_conversion_and_manifest_write_preserve_lineage_and_verify(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _context.workspace = workspace
    master_bytes = b"master"
    caption_bytes = (
        b"1\n00:00:00,000 --> 00:00:00,500\nThe last lightkeeper.\n\n"
        b"2\n00:00:00,500 --> 00:00:01,000\nA Northstar preview.\n"
    )
    originals = (
        _stored_asset(
            asset_id="asset_original_master",
            role=AssetRole.PROGRAM_MASTER,
            filename="master.mov",
            content=master_bytes,
            protection=AssetProtection.ORIGINAL,
            relative_path="originals/asset_original_master/master.mov",
        ),
        _stored_asset(
            asset_id="asset_original_captions",
            role=AssetRole.CAPTIONS,
            filename="captions.srt",
            content=caption_bytes,
            protection=AssetProtection.ORIGINAL,
            relative_path="originals/asset_original_captions/captions.srt",
        ),
    )
    working = (
        _stored_asset(
            asset_id="asset_working_master",
            role=AssetRole.PROGRAM_MASTER,
            filename="master.mov",
            content=master_bytes,
            protection=AssetProtection.WORKING,
            relative_path="working/asset_working_master/master.mov",
            predecessor="asset_original_master",
        ),
        _stored_asset(
            asset_id="asset_working_captions",
            role=AssetRole.CAPTIONS,
            filename="captions.srt",
            content=caption_bytes,
            protection=AssetProtection.WORKING,
            relative_path="working/asset_working_captions/captions.srt",
            predecessor="asset_original_captions",
        ),
    )
    for item, content in zip((*originals, *working), (master_bytes, caption_bytes, master_bytes, caption_bytes)):
        path = workspace / item.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    caption_payload = TOOL_REGISTRY["convert_caption_format"].input_model(
        asset_id="asset_working_captions",
        option_id="option_convert_captions",
        target_format="webvtt",
    )
    manifest_payload = TOOL_REGISTRY["write_checksum_manifest"].input_model(
        run_id=RUN_ID,
        option_id="option_write_manifest",
        format="sha256sums",
    )
    caption_option = RemediationOption.from_call(
        option_id=caption_payload.option_id,
        finding_id="finding_caption_format",
        tool="convert_caption_format",
        payload=caption_payload,
        tier=1,
        description="Convert captions to WebVTT without changing cues.",
        produces_derivative=False,
    )
    manifest_option = RemediationOption.from_call(
        option_id=manifest_payload.option_id,
        finding_id="finding_manifest",
        tool="write_checksum_manifest",
        payload=manifest_payload,
        tier=1,
        description="Write a SHA-256 manifest for the current package.",
        produces_derivative=False,
    )
    findings = (
        RuntimeFinding(
            finding_id=caption_option.finding_id,
            requirement_id="req_caption_format",
            asset_id="asset_working_captions",
            cycle_opened=0,
            status=FindingLifecycle.OPEN,
            options=(caption_option,),
            observed_rendered="srt",
            expected_rendered="webvtt",
        ),
        RuntimeFinding(
            finding_id=manifest_option.finding_id,
            requirement_id="req_manifest",
            asset_id="asset_working_master",
            cycle_opened=0,
            status=FindingLifecycle.OPEN,
            options=(manifest_option,),
            observed_rendered="absent",
            expected_rendered="present",
        ),
    )
    record = RuntimeRun(
        run_id=RUN_ID,
        profile_id="northstar_broadcast_master_v1",
        content_type="program",
        assets=(*originals, *working),
        findings=findings,
        original_hashes=tuple((item.asset.asset_id, item.asset.sha256) for item in originals),
    )
    state = RunStateSnapshot(run_id=RUN_ID, status=RunStatus.REMEDIATING)
    store = LocalDurableStore(tmp_path / "store")
    store.save_snapshot(state)
    store.save_runtime_run(record)
    authority_store = InMemoryAuthorityStore()
    executor = RemediationExecutor(
        workspace=workspace,
        store=store,
        authority_store=authority_store,
    )

    def authority_context(value: RuntimeRun) -> AuthorityRunContext:
        return AuthorityRunContext(
            run_id=RUN_ID,
            status=RunStatus.FINDINGS_READY,
            assets=tuple(item.asset for item in value.current_assets()),
            findings=tuple(
                PendingFinding(finding_id=item.finding_id, status=item.status)
                for item in findings
            ),
            options=(caption_option, manifest_option),
        )

    after_caption, caption_result = executor.execute(
        run=record,
        state=state,
        authority_context=authority_context(record),
        option=caption_option,
        payload=caption_payload,
        actor="SYSTEM",
    )
    after_manifest, manifest_result = executor.execute(
        run=after_caption,
        state=state,
        authority_context=authority_context(after_caption),
        option=manifest_option,
        payload=manifest_payload,
        actor="SYSTEM",
    )

    assert caption_result.status.value == "OK"
    assert manifest_result.status.value == "OK"
    converted = next(
        item for item in after_manifest.assets
        if item.asset.asset_id == caption_result.new_asset_id
    )
    assert converted.asset.predecessor == "asset_working_captions"
    assert converted.asset.relation is AssetRelation.CONVERTED_FROM
    inspection = execute_inspection(
        "inspect_captions",
        InspectCaptionsInput(asset_id=converted.asset.asset_id, program_duration_s=1),
        _context(after_manifest),
    )
    assert next(
        value.value for value in inspection.measurements
        if value.measurement_key == "captions.format"
    ) == "webvtt"
    verified = execute_inspection(
        "verify_manifest",
        VerifyManifestInput(run_id=RUN_ID),
        _context(after_manifest),
    )
    assert verified.status.value == "OK"
    assert verified.measurements[0].value is True
    assert (workspace / originals[1].relative_path).read_bytes() == caption_bytes
