"""Hand-authored model-output fixtures for deterministic M3 boundary tests."""

from __future__ import annotations

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
        "reason": "Hand-authored candidate fixture.",
        "confidence": 1,
        "proposed_disposition": None,
        "referenced_document": referenced_document,
    }


def northstar_batch(
    profile_id: str,
    segments: tuple[SourceSegment, ...],
) -> dict[str, object]:
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


def adversarial_batch(
    fixture_id: str,
    segments: tuple[SourceSegment, ...],
) -> dict[str, object]:
    segment = segments[0]
    if fixture_id == "ambiguous":
        items = [
            _candidate(
                segments, 1, 1, "audio.integrated_loudness", None, (), None,
                "PROGRAM_MASTER", "primary_audio", status="AMBIGUOUS"
            )
        ]
    elif fixture_id == "conditional_applicability":
        items = [
            _candidate(
                segments, 1, 1, "captions.format", "EQUALS", ("WebVTT",), None,
                "CAPTIONS", conditions=({
                    "field": "asset.dynamic_range_class",
                    "op": "EQ",
                    "values": ["HDR10+"],
                    "anchor_segment_id": segment.segment_id,
                },)
            )
        ]
    elif fixture_id == "contradictory":
        items = [
            _candidate(
                segments, 1, 1, "audio.integrated_loudness", "EQUALS", ("-24",),
                "LUFS", "PROGRAM_MASTER", "primary_audio"
            ),
            _candidate(
                segments, 2, 2, "audio.integrated_loudness", "EQUALS", ("-16",),
                "LUFS", "PROGRAM_MASTER", "primary_audio"
            ),
        ]
    elif fixture_id == "external_dependency":
        items = [
            _candidate(
                segments, 1, 1, None, None, (), None, "PROGRAM_MASTER",
                status="EXTERNAL_DEPENDENCY",
                referenced_document="https://northstar.example.invalid/external-reference",
            )
        ]
    elif fixture_id == "injected_instruction":
        items = [
            _candidate(
                segments, 1, 1, "agent.delete_original", "PRESENT", (), None,
                "OTHER", obligation="UNKNOWN", status="UNSUPPORTED"
            )
        ]
    elif fixture_id == "mixed_modals":
        items = [
            _candidate(
                segments, 1, 1, "audio.integrated_loudness", "EQUALS", ("-24",),
                "LUFS", "PROGRAM_MASTER", "primary_audio", obligation="UNKNOWN"
            )
        ]
    elif fixture_id == "unsupported":
        items = [
            _candidate(
                segments, 1, 1, "captions.semantic_confidence", "MIN", ("1",), None,
                "CAPTIONS", status="UNSUPPORTED"
            )
        ]
    else:
        raise ValueError(fixture_id)
    return {"requirements": items}
