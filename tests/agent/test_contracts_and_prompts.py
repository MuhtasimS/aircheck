"""Trust-boundary tests for M3 semantic inputs and prompts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError


def _document_and_segments():
    from aircheck.domain.source import ingest_source_document, segment_source_document
    from aircheck.domain.types import SourceKind

    document = ingest_source_document(
        doc_id="doc_prompt",
        run_id="run_m3_prompt",
        kind=SourceKind.SPEC,
        title="Prompt fixture",
        raw=b"Program audio MUST be 48 kHz.",
        ingested_at=datetime(2026, 9, 7, tzinfo=UTC),
    )
    return document, segment_source_document(document)


def test_package_inventory_rejects_paths_and_duplicate_asset_ids() -> None:
    from aircheck.agent.contracts import InventoryAsset, PackageInventory

    with pytest.raises(ValidationError, match="filename"):
        InventoryAsset(
            asset_id="asset_master",
            role="PROGRAM_MASTER",
            filename="C:/delivery/master.mov",
        )

    asset = InventoryAsset(
        asset_id="asset_master",
        role="PROGRAM_MASTER",
        filename="master.mov",
    )
    with pytest.raises(ValidationError, match="unique"):
        PackageInventory(
            run_id="run_m3_prompt",
            content_type="program",
            destination_profile="Northstar Broadcast Master",
            assets=(asset, asset),
        )


def test_candidate_batch_schema_has_no_path_offset_tool_action_or_verdict_slot() -> None:
    from aircheck.agent.contracts import CandidateRequirementBatch

    candidate = {
        "candidate_id": "candidate_audio",
        "segment_ids": ["doc_prompt:s0001"],
        "quote": "Program audio MUST be 48 kHz.",
        "proposed_obligation": "MUST",
        "measurement_key": "audio.sample_rate",
        "constraint_draft": {
            "operator": "EQUALS",
            "raw_values": ["48"],
            "raw_unit": "kHz",
        },
        "scope_draft": {"asset_role": "PROGRAM_MASTER", "stream": "primary_audio"},
        "applicability_draft": [],
        "proposed_status": "EXECUTABLE",
        "reason": "The cited clause states an exact sample rate.",
        "confidence": 1,
        "proposed_disposition": None,
        "referenced_document": None,
    }
    for forbidden in (
        "start_offset",
        "filesystem_path",
        "tool_call",
        "action",
        "terminal_verdict",
    ):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            CandidateRequirementBatch.model_validate(
                {"requirements": [{**candidate, forbidden: "untrusted"}]}
            )


def test_interpretation_prompt_delimits_source_and_exposes_closed_vocabularies() -> None:
    from aircheck.agent.contracts import InventoryAsset, PackageInventory
    from aircheck.agent.prompts import build_interpretation_prompt

    document, segments = _document_and_segments()
    inventory = PackageInventory(
        run_id="run_m3_prompt",
        content_type="program",
        destination_profile="Northstar Broadcast Master",
        assets=(
            InventoryAsset(
                asset_id="asset_master",
                role="PROGRAM_MASTER",
                filename="master.mov",
            ),
        ),
    )

    prompt = build_interpretation_prompt(document, segments, inventory)

    assert "<untrusted_source_segments>" in prompt
    assert "doc_prompt:s0001" in prompt
    assert "Program audio MUST be 48 kHz." in prompt
    assert "audio.sample_rate" in prompt
    assert "EQUALS" in prompt and "ABSENT" in prompt
    assert "asset.role" in prompt and "run.destination_profile" in prompt
    assert "The model must not supply offsets" in prompt
    assert "filesystem path" in prompt
    assert "untrusted.directive" in prompt
    assert "preserve the source field phrase" in prompt
    assert 'stream "primary_audio"' in prompt
    assert "package.filename[role]" in prompt
    assert "literal characters [role]" in prompt
    assert "captions.present and captions.format" in prompt
    assert "package.manifest_present and package.checksums_match" in prompt
    assert "use PRESENT, never EQUALS true" in prompt
    assert "Never propose CONTRADICTORY" in prompt
    assert "coordinator discretion is not a referenced document" in prompt
    assert "broadcast-quality loudness" in prompt
    assert "C:\\Users" not in prompt


def test_planning_prompt_preserves_mandatory_ids_and_forbids_authority_outputs() -> None:
    from aircheck.agent.prompts import build_planning_prompt
    from aircheck.domain.planning import RequiredCheck, ToolArgument

    checks = (
        RequiredCheck(
            check_id="check_audio",
            requirement_id="req_audio",
            asset_id="asset_master",
            tool="probe_media",
            tool_args=(ToolArgument(name="asset_id", value="asset_master"),),
        ),
    )

    prompt = build_planning_prompt(
        run_id="run_m3_prompt",
        cycle=0,
        required_checks=checks,
        asset_ids=("asset_master",),
    )

    assert "check_audio" in prompt and "req_audio" in prompt
    assert "every required check exactly once" in prompt
    assert "Tier-0" in prompt
    assert "Never propose remediation" in prompt
    assert "filesystem path" in prompt
    assert "DELIVERY_READY" in prompt
