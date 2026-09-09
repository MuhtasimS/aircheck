"""Bounded S1 invocation followed by deterministic requirement admission."""

from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError

from aircheck.agent.contracts import (
    CandidateRequirementBatch,
    PackageInventory,
    StructuredSemanticModel,
)
from aircheck.agent.prompts import build_interpretation_prompt
from aircheck.domain.admission import CandidateRequirement, Requirement, admit, mark_contradictions
from aircheck.domain.primitives import FrozenModel, NonEmptyStr
from aircheck.domain.source import SourceDocument, segment_source_document


class InterpretationResult(FrozenModel):
    candidates: tuple[CandidateRequirement, ...]
    requirements: tuple[Requirement, ...]
    attempts: int = Field(ge=1, le=2)
    validation_failures: tuple[NonEmptyStr, ...] = ()


class InterpretationExhaustedError(RuntimeError):
    def __init__(self, failures: tuple[str, ...]) -> None:
        super().__init__(f"S1 output rejected after {len(failures)} attempts")
        self.failures = failures


def _validate_batch(value: Any) -> CandidateRequirementBatch:
    if isinstance(value, CandidateRequirementBatch):
        return value
    if isinstance(value, str):
        return CandidateRequirementBatch.model_validate_json(value)
    return CandidateRequirementBatch.model_validate(value)


def interpret_requirements(
    model: StructuredSemanticModel,
    document: SourceDocument,
    inventory: PackageInventory,
    *,
    available_document_titles: tuple[str, ...] = (),
    max_attempts: int = 2,
) -> InterpretationResult:
    """Run S1 at most twice and admit only provenance-valid typed candidates."""

    if not 1 <= max_attempts <= 2:
        raise ValueError("S1 permits at most 2 attempts")
    if document.run_id != inventory.run_id:
        raise ValueError("source document and package inventory must share a run")

    segments = segment_source_document(document)
    failures: list[str] = []
    titles = tuple(dict.fromkeys((document.title, *available_document_titles)))
    for attempt in range(1, max_attempts + 1):
        prompt = build_interpretation_prompt(
            document,
            segments,
            inventory,
            retry_errors=tuple(failures[-1:]),
        )
        try:
            raw = model.generate(prompt, CandidateRequirementBatch)
        except Exception as exc:
            failures.append(f"PROVIDER_ERROR:{type(exc).__name__}")
            continue
        try:
            batch = _validate_batch(raw)
        except (TypeError, ValueError, ValidationError):
            failures.append("SCHEMA_INVALID")
            continue

        try:
            admitted = tuple(
                admit(
                    candidate,
                    document,
                    segments,
                    available_document_titles=titles,
                )
                for candidate in batch.requirements
            )
        except (TypeError, ValueError):
            failures.append("PROVENANCE_INVALID")
            continue

        return InterpretationResult(
            candidates=batch.requirements,
            requirements=mark_contradictions(admitted),
            attempts=attempt,
            validation_failures=tuple(failures),
        )

    raise InterpretationExhaustedError(tuple(failures))
