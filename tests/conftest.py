"""Shared domain fixtures built from literal synthetic ground truth."""

from copy import deepcopy

import pytest


@pytest.fixture
def canonical_run_data() -> dict[str, object]:
    """Return an isolated literal run requiring a protected audio decision."""

    fixture: dict[str, object] = {
        "run_id": "run_llk_broadcast_042",
        "program_id": "program_last_lightkeeper",
        "destination_profile": "Northstar Broadcast Master",
        "status": "AWAITING_HUMAN_DECISION",
        "created_at": "2026-09-03T18:41:00Z",
        "updated_at": "2026-09-03T18:47:08Z",
        "original_assets": [
            {
                "asset_id": "asset_program_master",
                "filename": "LAST_LIGHTKEEPER_MASTER_v7.mov",
                "role": "PROGRAM_MASTER",
                "media_type": "video/quicktime",
                "version": 7,
                "is_original": True,
            },
            {
                "asset_id": "asset_captions_en",
                "filename": "last-lightkeeper-final-en.srt",
                "role": "CAPTION_SIDECAR",
                "media_type": "application/x-subrip",
                "version": 1,
                "is_original": True,
            },
        ],
        "working_assets": [
            {
                "asset_id": "asset_caption_delivery",
                "filename": "LLK_NSBM_CC_EN.scc",
                "role": "CAPTION_SIDECAR",
                "media_type": "text/x-scc",
                "version": 1,
                "is_original": False,
                "derived_from": "asset_captions_en",
            }
        ],
        "requirements": [
            {
                "requirement_id": "req_audio_loudness",
                "source_reference": "Northstar Broadcast Master §4.2",
                "category": "AUDIO",
                "description": "Program loudness must measure -24 LKFS ±2 LU.",
                "normalized_constraint": {
                    "metric": "integrated_loudness_lkfs",
                    "target": -24,
                    "tolerance": 2,
                },
                "applicable_assets": ["asset_program_master"],
                "verification_tool": "measure_audio",
                "severity": "BLOCKER",
                "remediation_policy": "APPROVAL_REQUIRED",
            },
            {
                "requirement_id": "req_filename",
                "source_reference": "Northstar Broadcast Master §2.1",
                "category": "NAMING",
                "description": "Program master must follow the Northstar delivery token pattern.",
                "normalized_constraint": {
                    "pattern": "{program}_{destination}_{version}.mov"
                },
                "applicable_assets": ["asset_program_master"],
                "verification_tool": "verify_filename",
                "severity": "BLOCKER",
                "remediation_policy": "REVERSIBLE",
            },
            {
                "requirement_id": "req_captions",
                "source_reference": "Northstar Broadcast Master §6.3",
                "category": "CAPTIONS",
                "description": "English captions must be supplied as SCC sidecar media.",
                "normalized_constraint": {"format": "SCC", "language": "en"},
                "applicable_assets": ["asset_captions_en"],
                "verification_tool": "inspect_captions",
                "severity": "BLOCKER",
                "remediation_policy": "REVERSIBLE",
            },
        ],
        "qc_plan": ["req_filename", "req_audio_loudness", "req_captions"],
        "findings": [
            {
                "finding_id": "finding_filename",
                "requirement_id": "req_filename",
                "asset_id": "asset_program_master",
                "observed_value": "LAST_LIGHTKEEPER_MASTER_v7.mov",
                "expected_value": "LAST_LIGHTKEEPER_NSBM_v7.mov",
                "status": "FIXED",
                "evidence": [
                    {
                        "evidence_id": "evidence_filename_rename",
                        "kind": "TOOL_RESULT",
                        "label": "Delivery-copy rename result",
                        "uri": "evidence/filename-rename.json",
                    }
                ],
                "remediation_options": [
                    {
                        "tool_name": "rename_asset",
                        "label": "Rename delivery copy",
                        "policy": "REVERSIBLE",
                    }
                ],
            },
            {
                "finding_id": "finding_loudness",
                "requirement_id": "req_audio_loudness",
                "asset_id": "asset_program_master",
                "observed_value": -19.1,
                "expected_value": {"target": -24, "tolerance": 2},
                "status": "FAIL",
                "evidence": [
                    {
                        "evidence_id": "evidence_loudness_measurement",
                        "kind": "MEASUREMENT",
                        "label": "Integrated loudness measurement",
                        "uri": "evidence/audio-loudness.json",
                    }
                ],
                "remediation_options": [
                    {
                        "tool_name": "create_audio_delivery_derivative",
                        "label": "Create compliant audio derivative",
                        "policy": "APPROVAL_REQUIRED",
                    }
                ],
            },
        ],
        "pending_decision": {
            "decision_id": "decision_audio_derivative",
            "title": "Audio remediation requires approval",
            "reason": "The operation changes protected program content.",
            "tool_name": "create_audio_delivery_derivative",
            "asset_id": "asset_program_master",
            "status": "PENDING",
        },
        "events": [
            {
                "event_id": "evt_created",
                "run_id": "run_llk_broadcast_042",
                "timestamp": "2026-09-03T18:41:00Z",
                "event_type": "RUN_CREATED",
                "actor": "SYSTEM",
                "summary": "Delivery run created.",
                "evidence_refs": [],
            },
            {
                "event_id": "evt_spec",
                "run_id": "run_llk_broadcast_042",
                "timestamp": "2026-09-03T18:42:12Z",
                "event_type": "SPEC_INTERPRETED",
                "actor": "AIRCHECK",
                "summary": "Northstar Broadcast Master normalized into 12 requirements.",
                "evidence_refs": [],
            },
            {
                "event_id": "evt_audio",
                "run_id": "run_llk_broadcast_042",
                "timestamp": "2026-09-03T18:46:44Z",
                "event_type": "LOUDNESS_REQUIREMENT_FAILED",
                "actor": "AIRCHECK",
                "summary": "Program loudness measured -19.1 LUFS against -24 ±2 LUFS.",
                "evidence_refs": ["evidence_loudness_measurement"],
            },
            {
                "event_id": "evt_decision",
                "run_id": "run_llk_broadcast_042",
                "timestamp": "2026-09-03T18:47:08Z",
                "event_type": "HUMAN_DECISION_REQUIRED",
                "actor": "AIRCHECK",
                "summary": "Protected audio change paused for human authority.",
                "evidence_refs": ["evidence_loudness_measurement"],
            },
        ],
        "final_evidence": None,
    }
    return deepcopy(fixture)
