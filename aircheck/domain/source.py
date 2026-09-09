"""Deterministic source ingestion, segmentation, and provenance construction."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from pydantic import Field, ValidationInfo, field_validator, model_validator

from aircheck.domain.primitives import (
    DocumentId,
    FrozenModel,
    NonEmptyStr,
    RunId,
    SegmentId,
)
from aircheck.domain.types import SourceKind


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*#*\s*$")
_NUMBERED_HEADING = re.compile(
    r"^\s*(?P<number>[0-9]+(?:\.[0-9]+)+)[.)]?\s+(?P<title>[^.!?]+?)\s*$"
)
_SENTENCE = re.compile(r"\S.*?(?:[.!?](?=\s|$)|\Z)", re.DOTALL)
_SPAN_CONTEXT_KEY = "aircheck_source_span_factory"
_SPAN_CONTEXT_TOKEN = object()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def normalize_source(raw: bytes) -> str:
    """Normalize UTF-8 source bytes without discarding paragraph boundaries."""

    text = raw.decode("utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip(" \t") for line in text.split("\n"))


class SourceDocument(FrozenModel):
    doc_id: DocumentId
    run_id: RunId
    kind: SourceKind
    title: NonEmptyStr
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str
    ingested_at: datetime

    @field_validator("ingested_at")
    @classmethod
    def validate_ingested_at(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_normalized_hash(self) -> SourceDocument:
        if _sha256(self.text.encode("utf-8")) != self.normalized_sha256:
            raise ValueError("normalized_sha256 must match text")
        return self


class SourceSegment(FrozenModel):
    segment_id: SegmentId
    doc_id: DocumentId
    index: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: NonEmptyStr
    segment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_segment(self) -> SourceSegment:
        expected_id = f"{self.doc_id}:s{self.index:04d}"
        if self.segment_id != expected_id:
            raise ValueError("segment_id must match doc_id and index")
        if self.end <= self.start:
            raise ValueError("segment end must follow start")
        if _sha256(self.text.encode("utf-8")) != self.segment_sha256:
            raise ValueError("segment_sha256 must match text")
        return self


class SourceSpan(FrozenModel):
    doc_id: DocumentId
    segment_ids: tuple[SegmentId, ...] = Field(min_length=1, max_length=3)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: NonEmptyStr
    quote: NonEmptyStr
    reference: NonEmptyStr | None = None

    @model_validator(mode="before")
    @classmethod
    def require_runtime_factory(cls, value: Any, info: ValidationInfo) -> Any:
        context = info.context or {}
        if context.get(_SPAN_CONTEXT_KEY) is not _SPAN_CONTEXT_TOKEN:
            raise TypeError("SourceSpan must be created by build_source_span()")
        return value

    @model_validator(mode="after")
    def validate_offsets(self) -> SourceSpan:
        if self.end <= self.start:
            raise ValueError("span end must follow start")
        return self


def ingest_source_document(
    *,
    doc_id: str,
    run_id: str,
    kind: SourceKind,
    title: str,
    raw: bytes,
    ingested_at: datetime,
) -> SourceDocument:
    normalized = normalize_source(raw)
    return SourceDocument(
        doc_id=doc_id,
        run_id=run_id,
        kind=kind,
        title=title,
        raw_sha256=_sha256(raw),
        normalized_sha256=_sha256(normalized.encode("utf-8")),
        text=normalized,
        ingested_at=ingested_at,
    )


def segment_source_document(document: SourceDocument) -> tuple[SourceSegment, ...]:
    """Split Markdown/plain prose into deterministic sentence segments."""

    segments: list[SourceSegment] = []
    paragraph_parts: list[str] = []
    paragraph_start = 0
    reference: str | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph_parts
        if not paragraph_parts:
            return
        paragraph = "".join(paragraph_parts).rstrip("\n")
        for match in _SENTENCE.finditer(paragraph):
            sentence = match.group(0)
            index = len(segments) + 1
            start = paragraph_start + match.start()
            segments.append(
                SourceSegment(
                    segment_id=f"{document.doc_id}:s{index:04d}",
                    doc_id=document.doc_id,
                    index=index,
                    start=start,
                    end=paragraph_start + match.end(),
                    text=sentence,
                    segment_sha256=_sha256(sentence.encode("utf-8")),
                    reference=reference,
                )
            )
        paragraph_parts = []

    offset = 0
    for line in document.text.splitlines(keepends=True):
        content = line.removesuffix("\n")
        heading = _HEADING.match(content)
        numbered_heading = _NUMBERED_HEADING.match(content)
        if heading:
            flush_paragraph()
            reference = heading.group("title")
        elif numbered_heading:
            flush_paragraph()
            reference = (
                f"{numbered_heading.group('number')} {numbered_heading.group('title')}"
            )
        elif not content.strip():
            flush_paragraph()
        else:
            if not paragraph_parts:
                paragraph_start = offset
            paragraph_parts.append(line)
        offset += len(line)
    flush_paragraph()
    return tuple(segments)


def build_source_span(
    document: SourceDocument,
    segments: tuple[SourceSegment, ...],
    segment_ids: tuple[str, ...],
    *,
    quote: str,
) -> SourceSpan:
    """Validate cited segments and mint the trusted provenance atom."""

    if not 1 <= len(segment_ids) <= 3:
        raise ValueError("a source span requires one to three segments")
    by_id = {segment.segment_id: segment for segment in segments}
    try:
        selected = tuple(by_id[segment_id] for segment_id in segment_ids)
    except KeyError as exc:
        raise ValueError("source span cites an unknown segment") from exc

    if any(segment.doc_id != document.doc_id for segment in selected):
        raise ValueError("source span segments must belong to the document")
    if tuple(segment.index for segment in selected) != tuple(
        range(selected[0].index, selected[0].index + len(selected))
    ):
        raise ValueError("source span segments must be contiguous and ordered")
    for segment in selected:
        if document.text[segment.start : segment.end] != segment.text:
            raise ValueError("source segment offsets or text do not match the document")
        if _sha256(segment.text.encode("utf-8")) != segment.segment_sha256:
            raise ValueError("source segment hash does not match its text")

    start = selected[0].start
    end = selected[-1].end
    text = document.text[start:end]
    normalized_quote = " ".join(quote.split())
    if not normalized_quote or normalized_quote not in " ".join(text.split()):
        raise ValueError("source quote is not present in cited segment text")

    references = {segment.reference for segment in selected}
    reference = selected[0].reference if len(references) == 1 else None
    return SourceSpan.model_validate(
        {
            "doc_id": document.doc_id,
            "segment_ids": tuple(segment_ids),
            "start": start,
            "end": end,
            "text": text,
            "quote": quote,
            "reference": reference,
        },
        context={_SPAN_CONTEXT_KEY: _SPAN_CONTEXT_TOKEN},
    )
