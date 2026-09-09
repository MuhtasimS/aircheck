"""Bounded S3 diagnosis; runtime validation retains all authority."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import Field, ValidationError

from aircheck.agent.contracts import StructuredSemanticModel
from aircheck.domain.primitives import FindingId, FrozenModel, NonEmptyStr, OptionId
from aircheck.runtime.models import RuntimeRun


class DiagnosisDisposition(str, Enum):
    APPLIED = "APPLIED"
    ESCALATE = "ESCALATE"
    NO_ACTION = "NO_ACTION"


class DiagnosisItem(FrozenModel):
    finding_id: FindingId
    disposition: DiagnosisDisposition
    option_id: OptionId | None = None
    reason: NonEmptyStr


class Diagnosis(FrozenModel):
    items: tuple[DiagnosisItem, ...] = Field(min_length=1)


class DiagnosisRejected(ValueError):
    pass


def _parse(value: Any) -> Diagnosis:
    if isinstance(value, Diagnosis):
        return value
    if isinstance(value, str):
        return Diagnosis.model_validate_json(value)
    return Diagnosis.model_validate(value)


def diagnose_findings(model: StructuredSemanticModel, run: RuntimeRun) -> Diagnosis:
    """Accept only exact, currently-open option references from untrusted S3 output."""

    open_findings = tuple(finding for finding in run.findings if finding.status.value == "OPEN")
    tier_one_available = any(
        option.tier == 1
        for finding in open_findings
        for option in finding.options
    )
    exposed_options = {
        finding.finding_id: tuple(
            option
            for option in finding.options
            if option.tier == (1 if tier_one_available else 2)
        )
        for finding in open_findings
    }
    prompt = json.dumps(
        {
            "role": "AIRCheck S3; recommend only from the supplied bound options",
            "prohibitions": [
                "no filesystem paths",
                "no authority decisions",
                "no correctness or terminal verdicts",
            ],
            "exposed_tools": [
                "read_finding",
                "read_measurement",
                "compare_requirement",
                *sorted(
                    {
                        option.tool
                        for options in exposed_options.values()
                        for option in options
                    }
                ),
            ],
            "findings": [
                {
                    "finding_id": finding.finding_id,
                    "observed": finding.observed_rendered,
                    "expected": finding.expected_rendered,
                    "options": [
                        {
                            "option_id": option.option_id,
                            "tool": option.tool,
                            "tier": option.tier,
                            "description": option.description_rendered,
                        }
                        for option in exposed_options[finding.finding_id]
                    ],
                }
                for finding in open_findings
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        diagnosis = _parse(model.generate(prompt, Diagnosis))
    except (TypeError, ValueError, ValidationError) as exc:
        raise DiagnosisRejected("S3 output is not a valid Diagnosis") from exc

    expected_ids = {finding.finding_id for finding in open_findings}
    received_ids = tuple(item.finding_id for item in diagnosis.items)
    if len(received_ids) != len(set(received_ids)) or set(received_ids) != expected_ids:
        raise DiagnosisRejected("S3 must diagnose every open finding exactly once")
    by_id = {finding.finding_id: finding for finding in open_findings}
    for item in diagnosis.items:
        finding = by_id[item.finding_id]
        option = next(
            (
                candidate
                for candidate in exposed_options[finding.finding_id]
                if candidate.option_id == item.option_id
            ),
            None,
        )
        if item.disposition is DiagnosisDisposition.NO_ACTION:
            if item.option_id is not None:
                raise DiagnosisRejected("NO_ACTION cannot reference an option")
        elif option is None:
            raise DiagnosisRejected("diagnosis references an unavailable option")
        elif item.disposition is DiagnosisDisposition.APPLIED and option.tier != 1:
            raise DiagnosisRejected("APPLIED requires a Tier-1 option")
        elif item.disposition is DiagnosisDisposition.ESCALATE and option.tier != 2:
            raise DiagnosisRejected("ESCALATE requires a Tier-2 option")
    return diagnosis


__all__ = (
    "Diagnosis",
    "DiagnosisDisposition",
    "DiagnosisItem",
    "DiagnosisRejected",
    "diagnose_findings",
)
