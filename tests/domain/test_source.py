"""R2.4 golden and negative tests for specification provenance."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aircheck.domain.source import (
    SourceSpan,
    build_source_span,
    ingest_source_document,
    segment_source_document,
)
from aircheck.domain.types import SourceKind


RAW_SOURCE = (
    b"# Northstar Master\r\n\r\n"
    b"## 4.2 Audio  \r\n"
    b"Program loudness must be -24 LKFS. "
    b"True peak must not exceed -2 dBTP.   \r\n\r\n"
    b"Captions should be present.\r\n"
)
NORMALIZED_SOURCE = (
    "# Northstar Master\n\n"
    "## 4.2 Audio\n"
    "Program loudness must be -24 LKFS. "
    "True peak must not exceed -2 dBTP.\n\n"
    "Captions should be present.\n"
)


@pytest.fixture
def source_document():
    return ingest_source_document(
        doc_id="doc_northstar_audio",
        run_id="run_source_001",
        kind=SourceKind.SPEC,
        title="Northstar audio",
        raw=RAW_SOURCE,
        ingested_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )


def test_source_normalization_and_hashes_are_golden(source_document) -> None:
    """Platform line endings and trailing spaces must not change admitted truth."""
    assert source_document.text == NORMALIZED_SOURCE
    assert source_document.raw_sha256 == (
        "ba5e2754989ccbb5d00c5d71a2c77d0f99d78f9ecf3c173c02f8a76e9e0f8fbe"
    )
    assert source_document.normalized_sha256 == (
        "21a2bc61967346aaabdda4258e82e8548dd091432a9edaf1f866c8edd89a4746"
    )


def test_segmenter_produces_stable_sentence_offsets_and_references(
    source_document,
) -> None:
    """A segmentation change must be deliberate because requirement IDs depend on it."""
    segments = segment_source_document(source_document)

    assert tuple(
        (segment.segment_id, segment.start, segment.end, segment.text, segment.reference)
        for segment in segments
    ) == (
        (
            "doc_northstar_audio:s0001",
            33,
            67,
            "Program loudness must be -24 LKFS.",
            "4.2 Audio",
        ),
        (
            "doc_northstar_audio:s0002",
            68,
            102,
            "True peak must not exceed -2 dBTP.",
            "4.2 Audio",
        ),
        (
            "doc_northstar_audio:s0003",
            104,
            131,
            "Captions should be present.",
            "4.2 Audio",
        ),
    )
    assert tuple(segment.segment_sha256 for segment in segments) == (
        "67be9e0ce5afbe3cea03a7754bc490a0dcd8bce386f3b9a7c27d29ce544158c8",
        "651f002d1aadee788b7bac8e4eaefb635761117c4f5d7b801fa208b78cd26121",
        "d9e903c08b49dbeac78f4f3f0fa8a463dd163292c315456a4f5543d628eb56d8",
    )


def test_plain_numbered_clause_becomes_reference_context() -> None:
    """Plain-text specifications must retain clause identity without Markdown."""
    document = ingest_source_document(
        doc_id="doc_plain_clause",
        run_id="run_source_001",
        kind=SourceKind.SPEC,
        title="Plain specification",
        raw=b"4.2 Audio\nProgram loudness must be -24 LKFS.\n",
        ingested_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )

    segments = segment_source_document(document)

    assert len(segments) == 1
    assert segments[0].text == "Program loudness must be -24 LKFS."
    assert segments[0].reference == "4.2 Audio"


def test_runtime_builds_a_contiguous_span_and_normalizes_the_quote(
    source_document,
) -> None:
    segments = segment_source_document(source_document)

    span = build_source_span(
        source_document,
        segments,
        (segments[0].segment_id, segments[1].segment_id),
        quote="Program loudness must be -24 LKFS.\nTrue peak must not exceed -2 dBTP.",
    )

    assert span.start == 33
    assert span.end == 102
    assert span.text == NORMALIZED_SOURCE[33:102]
    assert span.reference == "4.2 Audio"


def test_source_span_cannot_be_constructed_outside_runtime() -> None:
    """Untrusted callers must not mint apparently verified provenance."""
    with pytest.raises(TypeError, match="build_source_span"):
        SourceSpan(
            doc_id="doc_northstar_audio",
            segment_ids=("doc_northstar_audio:s0001",),
            start=0,
            end=4,
            text="fake",
            quote="fake",
            reference=None,
        )


@pytest.mark.parametrize("failure", ["quote", "noncontiguous", "tampered"])
def test_span_builder_rejects_false_or_broken_provenance(
    source_document,
    failure: str,
) -> None:
    segments = segment_source_document(source_document)
    selected = (segments[0].segment_id,)
    quote = "Program loudness must be -24 LKFS."
    supplied_segments = segments

    if failure == "quote":
        quote = "Program loudness is definitely compliant."
    elif failure == "noncontiguous":
        selected = (segments[0].segment_id, segments[2].segment_id)
    else:
        supplied_segments = (
            segments[0].model_copy(update={"text": "tampered"}),
            *segments[1:],
        )

    with pytest.raises(ValueError):
        build_source_span(
            source_document,
            supplied_segments,
            selected,
            quote=quote,
        )
