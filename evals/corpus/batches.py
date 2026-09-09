"""Deterministic typed S1 candidate batches for the M6 corpus.

These are corpus-owned, catalog-valid candidate requirement batches. The two
product profiles reproduce the canonical M2/M3 Northstar batches verbatim (a
harness test pins them byte-for-byte to ``tests.agent.fixtures`` so they cannot
drift from proven M3/M4 ground truth). The focused batches are strict subsets of
those same rows, plus a small number of additional catalog measurements used to
isolate a single technical defect. The adversarial batches reproduce the frozen
M3 adversarial candidates.

A "batch" is the untrusted S1 output. Admission, applicability, severity, and
executability are decided by the deterministic runtime, never by these fixtures.
"""

from __future__ import annotations

from aircheck.domain.source import SourceSegment


# Row shape: (segment_index, measurement_key, operator, values, unit, role, stream)
_Row = tuple[int, str | None, str | None, tuple[str, ...], str | None, str, str | None]


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
        "reason": "Corpus deterministic candidate fixture.",
        "confidence": 1,
        "proposed_disposition": None,
        "referenced_document": referenced_document,
    }


_SHARED_ROWS: tuple[_Row, ...] = (
    (2, "container.format", "EQUALS", ("QuickTime MOV",), None, "PROGRAM_MASTER", None),
    (2, "video.codec", "EQUALS", ("H.264",), None, "PROGRAM_MASTER", "video"),
    (2, "video.width", "EQUALS", ("1920",), "pixels", "PROGRAM_MASTER", "video"),
    (2, "video.height", "EQUALS", ("1080",), "pixels", "PROGRAM_MASTER", "video"),
    (2, "video.frame_rate", "EQUALS", ("24000/1001",), "fps", "PROGRAM_MASTER", "video"),
    (3, "audio.channel_count", "EQUALS", ("2",), None, "PROGRAM_MASTER", "primary_audio"),
    (3, "audio.channel_layout", "EQUALS", ("stereo",), None, "PROGRAM_MASTER", "primary_audio"),
    (3, "audio.sample_rate", "EQUALS", ("48000",), "Hz", "PROGRAM_MASTER", "primary_audio"),
)

_BROADCAST_ROWS: tuple[_Row, ...] = (
    (4, "audio.integrated_loudness", "RANGE", ("-26", "-22"), "LUFS", "PROGRAM_MASTER", "primary_audio"),
    (5, "audio.true_peak", "MAX", ("-2",), "dBTP", "PROGRAM_MASTER", "primary_audio"),
    (6, "captions.present", "PRESENT", (), None, "CAPTIONS", None),
    (6, "captions.format", "EQUALS", ("WebVTT",), None, "CAPTIONS", None),
    (7, "package.filename[role]", "EQUALS", ("THE_LAST_LIGHTKEEPER_NSBM_v1.mov",), None, "PROGRAM_MASTER", None),
    (8, "package.manifest_present", "PRESENT", (), None, "MANIFEST", None),
    (8, "package.checksums_match", "EQUALS", ("true",), None, "MANIFEST", None),
)

_PREVIEW_ROWS: tuple[_Row, ...] = (
    (4, "audio.integrated_loudness", "RANGE", ("-21", "-17"), "LUFS", "PROGRAM_MASTER", "primary_audio"),
    (5, "audio.true_peak", "MAX", ("-2",), "dBTP", "PROGRAM_MASTER", "primary_audio"),
    (6, "captions.present", "PRESENT", (), None, "CAPTIONS", None),
    (6, "captions.format", "ONE_OF", ("SRT", "WebVTT"), None, "CAPTIONS", None),
    (7, "package.filename[role]", "EQUALS", ("the-last-lightkeeper-preview-v1.mov",), None, "PROGRAM_MASTER", None),
)


def northstar_batch(
    profile_id: str,
    segments: tuple[SourceSegment, ...],
) -> dict[str, object]:
    """Reproduce the canonical Northstar product candidate batch for one profile."""

    if profile_id == "northstar_broadcast_master_v1":
        rows = _SHARED_ROWS + _BROADCAST_ROWS
    elif profile_id == "northstar_digital_preview_v1":
        rows = _SHARED_ROWS + _PREVIEW_ROWS
    else:
        raise ValueError(profile_id)
    return {
        "requirements": [
            _candidate(segments, number, *row)
            for number, row in enumerate(rows, start=1)
        ]
    }


# Focused single-defect batches. Each is a strict, catalog-valid subset of, or a
# minimal extension to, the broadcast rows above, citing broadcast_master.md
# segments so admission/severity behave identically to the proven product runs.
_FOCUSED_ROWS: dict[str, tuple[_Row, ...]] = {
    # captions present + WebVTT required; isolates the caption-format defect.
    "caption_format": (
        (6, "captions.present", "PRESENT", (), None, "CAPTIONS", None),
        (6, "captions.format", "EQUALS", ("WebVTT",), None, "CAPTIONS", None),
    ),
    # manifest present + checksums; isolates the missing-manifest defect.
    "missing_manifest": (
        (8, "package.manifest_present", "PRESENT", (), None, "MANIFEST", None),
        (8, "package.checksums_match", "EQUALS", ("true",), None, "MANIFEST", None),
    ),
    # loudness within the broadcast window; isolates the Tier-2 loudness defect.
    "loudness": (
        (4, "audio.integrated_loudness", "RANGE", ("-26", "-22"), "LUFS", "PROGRAM_MASTER", "primary_audio"),
    ),
    # captions must be present; isolates the missing-component defect.
    "captions_present": (
        (6, "captions.present", "PRESENT", (), None, "CAPTIONS", None),
    ),
    # video codec; isolates an unfixable (report-only) codec defect.
    "codec": (
        (2, "video.codec", "EQUALS", ("H.264",), None, "PROGRAM_MASTER", "video"),
    ),
    # video frame rate; isolates an unfixable frame-rate defect.
    "frame_rate": (
        (2, "video.frame_rate", "EQUALS", ("24000/1001",), "fps", "PROGRAM_MASTER", "video"),
    ),
    # audio channel count; isolates an unfixable channel-count defect.
    "channel_count": (
        (3, "audio.channel_count", "EQUALS", ("2",), None, "PROGRAM_MASTER", "primary_audio"),
    ),
    # caption cue overlap; isolates an unfixable caption-timing defect.
    "caption_timing": (
        (6, "captions.overlapping_cues", "MAX", ("0",), None, "CAPTIONS", None),
    ),
    # manifest present + checksums; used with a corrupt foreign manifest to
    # isolate a report-only checksum mismatch.
    "checksum_match": (
        (8, "package.manifest_present", "PRESENT", (), None, "MANIFEST", None),
        (8, "package.checksums_match", "EQUALS", ("true",), None, "MANIFEST", None),
    ),
}


def focused_batch(
    focus: str,
    segments: tuple[SourceSegment, ...],
) -> dict[str, object]:
    """Build a focused single-defect candidate batch from broadcast_master.md."""

    try:
        rows = _FOCUSED_ROWS[focus]
    except KeyError as exc:
        raise ValueError(f"unknown focused batch: {focus}") from exc
    return {
        "requirements": [
            _candidate(segments, number, *row)
            for number, row in enumerate(rows, start=1)
        ]
    }


def conditional_batch(
    kind: str,
    segments: tuple[SourceSegment, ...],
) -> dict[str, object]:
    """A single content_type-conditional loudness requirement for the v1 grammar.

    ``supported`` cites the source polarity the grammar can prove (NEQ via
    "except promotional material") and admits APPLICABLE with the condition kept.
    ``reversed`` proposes the opposite polarity (EQ) the source does not support and
    is downgraded to UNRESOLVED (APPLICABILITY_POLARITY_UNPROVEN). Neither exercises
    runtime NOT_APPLICABLE resolution, which v1 does not implement (documented gap).
    """

    segment = segments[0]
    if kind == "supported":
        op = "NEQ"
    elif kind == "reversed":
        op = "EQ"
    else:
        raise ValueError(kind)
    condition = {
        "field": "run.content_type",
        "op": op,
        "values": ["promotional material"],
        "anchor_segment_id": segment.segment_id,
    }
    return {
        "requirements": [
            _candidate(
                segments, 1, 1, "audio.integrated_loudness", "RANGE", ("-26", "-22"),
                "LUFS", "PROGRAM_MASTER", "primary_audio", conditions=(condition,),
            )
        ]
    }


def adversarial_batch(
    fixture_id: str,
    segments: tuple[SourceSegment, ...],
) -> dict[str, object]:
    """Reproduce the frozen M3 adversarial candidate batch for one fixture."""

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


__all__ = ("northstar_batch", "focused_batch", "conditional_batch", "adversarial_batch")
