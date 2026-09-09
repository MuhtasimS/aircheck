"""Deterministic demo semantic boundary for the M5 application API.

The canonical M5 demonstration must run provider-free: the runtime owns every
measurement, predicate, authority decision, side effect, and terminal verdict,
while the semantic agent only interprets source prose into *untrusted* candidate
requirements, proposes plan ordering, and diagnoses/selects remediation options.

This module supplies a self-contained, deterministic ``StructuredSemanticModel``
double plus the hand-authored Northstar candidate batch that drives it. It is
demo/synthetic setup input only: it constructs no trusted requirement, owns no
predicate/terminal truth, and never mutates state. The runtime's deterministic
admission, planning, predicate evaluation, authority, and terminal computation
remain the sole source of truth.

It deliberately does not import the pytest ``tests`` package so the deployable
application boundary has no test-runtime dependency.
"""

from __future__ import annotations

import json
from typing import Any

from aircheck.agent.contracts import CandidateRequirementBatch
from aircheck.agent.diagnosis import Diagnosis
from aircheck.domain.planning import PlanProposal
from aircheck.domain.source import SourceSegment


def _candidate(
    segments: tuple[SourceSegment, ...],
    number: int,
    segment_index: int,
    measurement_key: str | None,
    operator: str | None,
    values: tuple[str, ...],
    unit: str | None,
    role: str,
    stream: str | None = None,
    *,
    obligation: str = "MUST",
    status: str = "EXECUTABLE",
    conditions: tuple[dict[str, object], ...] = (),
    referenced_document: str | None = None,
) -> dict[str, object]:
    segment = segments[segment_index - 1]
    return {
        "candidate_id": f"candidate_{number:03d}",
        "segment_ids": [segment.segment_id],
        "quote": segment.text,
        "proposed_obligation": obligation,
        "measurement_key": measurement_key,
        "constraint_draft": None
        if operator is None
        else {"operator": operator, "raw_values": list(values), "raw_unit": unit},
        "scope_draft": {"asset_role": role, "stream": stream},
        "applicability_draft": list(conditions),
        "proposed_status": status,
        "reason": "Hand-authored Northstar candidate fixture.",
        "confidence": 1,
        "proposed_disposition": None,
        "referenced_document": referenced_document,
    }


def northstar_demo_batch(
    profile_id: str,
    segments: tuple[SourceSegment, ...],
) -> dict[str, object]:
    """Deterministic untrusted S1 candidate batch for the two Northstar profiles.

    This is demo interpretation *input*; deterministic admission in the runtime
    decides which candidates become trusted requirements, with what severity,
    applicability, and normalization status.
    """
    shared = [
        (2, "container.format", "EQUALS", ("QuickTime MOV",), None, "PROGRAM_MASTER", None),
        (2, "video.codec", "EQUALS", ("H.264",), None, "PROGRAM_MASTER", "video"),
        (2, "video.width", "EQUALS", ("1920",), "pixels", "PROGRAM_MASTER", "video"),
        (2, "video.height", "EQUALS", ("1080",), "pixels", "PROGRAM_MASTER", "video"),
        (2, "video.frame_rate", "EQUALS", ("24000/1001",), "fps", "PROGRAM_MASTER", "video"),
        (3, "audio.channel_count", "EQUALS", ("2",), None, "PROGRAM_MASTER", "primary_audio"),
        (3, "audio.channel_layout", "EQUALS", ("stereo",), None, "PROGRAM_MASTER", "primary_audio"),
        (3, "audio.sample_rate", "EQUALS", ("48000",), "Hz", "PROGRAM_MASTER", "primary_audio"),
    ]
    if profile_id == "northstar_broadcast_master_v1":
        specific = [
            (4, "audio.integrated_loudness", "RANGE", ("-26", "-22"), "LUFS", "PROGRAM_MASTER", "primary_audio"),
            (5, "audio.true_peak", "MAX", ("-2",), "dBTP", "PROGRAM_MASTER", "primary_audio"),
            (6, "captions.present", "PRESENT", (), None, "CAPTIONS", None),
            (6, "captions.format", "EQUALS", ("WebVTT",), None, "CAPTIONS", None),
            (7, "package.filename[role]", "EQUALS", ("THE_LAST_LIGHTKEEPER_NSBM_v1.mov",), None, "PROGRAM_MASTER", None),
            (8, "package.manifest_present", "PRESENT", (), None, "MANIFEST", None),
            (8, "package.checksums_match", "EQUALS", ("true",), None, "MANIFEST", None),
        ]
    elif profile_id == "northstar_digital_preview_v1":
        specific = [
            (4, "audio.integrated_loudness", "RANGE", ("-21", "-17"), "LUFS", "PROGRAM_MASTER", "primary_audio"),
            (5, "audio.true_peak", "MAX", ("-2",), "dBTP", "PROGRAM_MASTER", "primary_audio"),
            (6, "captions.present", "PRESENT", (), None, "CAPTIONS", None),
            (6, "captions.format", "ONE_OF", ("SRT", "WebVTT"), None, "CAPTIONS", None),
            (7, "package.filename[role]", "EQUALS", ("the-last-lightkeeper-preview-v1.mov",), None, "PROGRAM_MASTER", None),
        ]
    else:
        raise ValueError(profile_id)
    rows = shared + specific
    return {
        "requirements": [
            _candidate(segments, number, *row)
            for number, row in enumerate(rows, start=1)
        ]
    }


class DemoSemanticModel:
    """Typed deterministic semantic double for the provider-free M5 demo.

    Authority and correctness stay in the deterministic runtime; this object only
    returns bounded structured outputs at S1/S2/S3 exactly as a real provider
    would, so the runtime can admit, plan, diagnose, authorize, execute, and
    terminate on its own.
    """

    def __init__(self, *, profile_id: str, segments: tuple[SourceSegment, ...]) -> None:
        self._profile_id = profile_id
        self._segments = segments
        self.calls: list[type] = []

    def generate(self, prompt: str, output_model: type) -> Any:
        self.calls.append(output_model)
        if output_model is CandidateRequirementBatch:
            return northstar_demo_batch(self._profile_id, self._segments)
        if output_model is PlanProposal:
            # Empty proposal forces the runtime's deterministic fallback plan,
            # which preserves mandatory coverage without model ordering.
            return {"ordered_items": []}
        if output_model is Diagnosis:
            payload = json.loads(prompt)
            items = []
            for finding in payload["findings"]:
                options = finding["options"]
                tier_one = next((item for item in options if item["tier"] == 1), None)
                tier_two = next((item for item in options if item["tier"] == 2), None)
                if tier_one is not None:
                    disposition = "APPLIED"
                    option_id = tier_one["option_id"]
                elif tier_two is not None:
                    disposition = "ESCALATE"
                    option_id = tier_two["option_id"]
                else:
                    disposition = "NO_ACTION"
                    option_id = None
                items.append(
                    {
                        "finding_id": finding["finding_id"],
                        "disposition": disposition,
                        "option_id": option_id,
                        "reason": "Bound deterministic demo diagnosis.",
                    }
                )
            return {"items": items}
        raise AssertionError(output_model)


__all__ = ["DemoSemanticModel", "northstar_demo_batch"]
