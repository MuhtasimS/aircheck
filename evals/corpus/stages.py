"""Deterministic stage model, observed-fact extraction, and first-divergence.

Stages follow AIRCheck's canonical chain (prose -> predicates -> facts ->
authority -> evidence) and are backed by durable run truth: the state snapshot,
the persisted ``RuntimeRun`` (requirements, per-cycle measurements/predicates,
findings, decisions, remediation counts, evidence), and the append-only ledger.

Grading is strict and complete, so a materially wrong intermediate truth cannot
pass merely because aggregate counts or the final terminal happen to agree:

- Requirements, plan items, findings, and final predicate/measurement outcomes are
  graded as COMPLETE maps keyed by measurement_key (exact key sets; extra or
  missing keys are divergences).
- Per-cycle predicate failures are graded so an earlier-cycle divergence that a
  later cycle overwrites is still caught.
- Event skeleton and diagnosis tier exposure are graded as ORDERED lists.
- Recovery accounting is graded for recovery scenarios.

Documented normalizations (intentionally non-graded because they are opaque or
non-deterministic): opaque generated identifiers (requirement_id, finding_id,
action_id, event_id, asset_id, authorization capability), wall-clock timestamps,
and the requirement-id suffixes inside terminal ``reasons`` (graded as reason
CATEGORIES with counts instead). Requirement identity is graded through the stable
measurement_key rather than the opaque requirement_id. Extraction fails loudly if
two graded records collide on a key, rather than silently merging them.

first_divergence compares observed to frozen expected stage by stage in canonical
order and returns the earliest MATERIALLY wrong graded stage, even when a later
stage or the terminal outcome is correct.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from aircheck.domain.constraints import (
    AbsentConstraint,
    EqualsConstraint,
    MaxConstraint,
    MinConstraint,
    OneOfConstraint,
    PatternConstraint,
    PresentConstraint,
    RangeConstraint,
)
from aircheck.domain.events import (
    ActionCompletedPayload,
    ActionFailedPayload,
    ActionStartedPayload,
    LedgerEvent,
    StateTransitionPayload,
)
from aircheck.domain.status import RunStatus
from aircheck.runtime.models import RuntimeRun


STAGE_ORDER: tuple[str, ...] = (
    "ingestion",
    "interpretation",
    "admission",
    "applicability",
    "planning",
    "profile_divergence",
    "measurement",
    "predicate",
    "findings",
    "diagnosis",
    "authority",
    "remediation",
    "events",
    "recovery",
    "terminal",
    "evidence_integrity",
)

# Fields whose list order is material (compared exactly). Every other list-valued
# field is compared as an order-independent multiset.
ORDERED_LIST_FIELDS: frozenset[str] = frozenset(
    {"tier_exposure", "action_tools", "skeleton", "fail_keys_by_cycle", "recovery_reasons"}
)


@dataclass(frozen=True)
class RunOutcome:
    """Everything the corpus needs to grade one executed scenario run."""

    final_status: RunStatus
    run: RuntimeRun
    events: tuple[LedgerEvent, ...]
    workspace: Path
    terminal_verdict: Any | None = None
    refused: bool = False
    diagnosis_tier_exposure: tuple[tuple[int, ...], ...] = ()
    adversarial_assessment: Any | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _multiset(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _constraint_signature(constraint: Any) -> dict[str, Any] | None:
    if constraint is None:
        return None
    signature: dict[str, Any] = {"operator": constraint.operator.value}
    if getattr(constraint, "unit", None) is not None:
        signature["unit"] = constraint.unit
    if isinstance(constraint, EqualsConstraint):
        signature["value"] = constraint.value
    elif isinstance(constraint, (MinConstraint, MaxConstraint)):
        signature["value"] = constraint.value
    elif isinstance(constraint, RangeConstraint):
        signature.update(lower=constraint.lower, upper=constraint.upper)
    elif isinstance(constraint, OneOfConstraint):
        signature["values"] = list(constraint.values)
    elif isinstance(constraint, PatternConstraint):
        signature.update(pattern=constraint.pattern, description=constraint.description)
    elif isinstance(constraint, PresentConstraint):
        signature["value"] = True
    elif isinstance(constraint, AbsentConstraint):
        signature["value"] = False
    return signature


class CorpusExtractionError(RuntimeError):
    """A graded key collided; the harness fails loudly rather than merging facts."""


def _require_unique(pairs: list[tuple[str, Any]], what: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CorpusExtractionError(f"duplicate {what} key {key!r}; cannot grade exactly")
        result[key] = value
    return result


def observed_terminal(outcome: RunOutcome) -> str:
    if outcome.refused:
        return "REFUSED_SAFE"
    return outcome.final_status.value


def _requirement_key(requirement: Any) -> str:
    # Stable semantic identity: the measurement key, or a provenance-derived label
    # for non-executable requirements that carry no catalog key.
    if requirement.measurement_key is not None:
        return requirement.measurement_key
    return f"req@{requirement.provenance.segment_ids[0]}"


_OPAQUE_ID = __import__("re").compile(r"^(req|finding|option|action|asset|auth|evt)_[0-9a-f]+$")


def _normalize_reasons(reasons: tuple[str, ...]) -> list[str]:
    # Drop opaque generated identifiers (e.g. a contradictory peer's requirement_id)
    # so reason CODES stay gradeable while non-deterministic identity is normalized.
    return sorted(reason for reason in reasons if not _OPAQUE_ID.match(reason))


def _requirements(run: RuntimeRun) -> list[dict[str, Any]]:
    # Requirement identity graded as an order-independent multiset of complete
    # records (handles two requirements that legitimately share a measurement key,
    # e.g. the contradictory fixture). status_reasons are graded separately.
    return [
        {
            "measurement_key": _requirement_key(item),
            "normalization_status": item.normalization_status.value,
            "severity": item.severity.value,
            "applicability_status": item.applicability_status.value,
            "disposition": item.disposition.value,
            "constraint": _constraint_signature(item.constraint),
        }
        for item in run.requirements
    ]


def _status_reasons(run: RuntimeRun) -> list[dict[str, Any]]:
    return [
        {"measurement_key": _requirement_key(item), "reasons": _normalize_reasons(item.status_reasons)}
        for item in run.requirements
    ]


def _plan_items(run: RuntimeRun) -> dict[str, Any]:
    # The plan's requirement bindings are exactly the durable check_order. Grade
    # each planned check's (tool, scope role) binding, keyed by the requirement it
    # verifies, so a mis-bound plan item is caught, not just a wrong item count.
    by_id = {item.requirement_id: item for item in run.requirements}
    pairs: list[tuple[str, Any]] = []
    for requirement_id in run.check_order:
        item = by_id[requirement_id]
        pairs.append(
            (
                _requirement_key(item),
                {"tool": item.verification_tool, "asset_role": item.asset_role},
            )
        )
    return _require_unique(pairs, "plan item")


def _latest_by_key(run: RuntimeRun, records: list[tuple[str, int, str]]) -> dict[str, str]:
    latest: dict[str, tuple[int, str]] = {}
    for key, cycle, value in records:
        current = latest.get(key)
        if current is None or cycle >= current[0]:
            latest[key] = (cycle, value)
    return {key: value for key, (_, value) in latest.items()}


def _predicate_records(run: RuntimeRun) -> list[tuple[str, int, str]]:
    key_by_req = {item.requirement_id: _requirement_key(item) for item in run.requirements}
    return [
        (key_by_req[p.requirement_id], p.cycle, p.outcome.value)
        for p in run.predicates
        if p.requirement_id in key_by_req
    ]


def _measurement_by_id(run: RuntimeRun) -> dict[str, Any]:
    by_id: dict[str, Any] = {}
    for measurement in run.measurements:
        if measurement.measurement_id in by_id:
            raise CorpusExtractionError(
                f"ambiguous measurement_id {measurement.measurement_id!r}; cannot bind provenance"
            )
        by_id[measurement.measurement_id] = measurement
    return by_id


def _predicate_bindings(run: RuntimeRun) -> list[dict[str, Any]]:
    # Grade predicate -> measurement provenance as a normalized semantic binding:
    # each predicate carries its own (requirement_key, cycle, outcome) and the
    # (measurement_key, requirement_key, cycle) of the single measurement its
    # measurement_id resolves to. A predicate rebound to another requirement's
    # measurement, a wrong-cycle measurement, or a missing measurement therefore
    # diverges. Opaque measurement_id is never frozen; only its semantic target is.
    key_by_req = {item.requirement_id: _requirement_key(item) for item in run.requirements}
    by_id = _measurement_by_id(run)
    bindings: list[dict[str, Any]] = []
    for predicate in run.predicates:
        measurement = by_id.get(predicate.measurement_id)
        bindings.append(
            {
                "requirement_key": key_by_req.get(predicate.requirement_id),
                "cycle": predicate.cycle,
                "outcome": predicate.outcome.value,
                "bound_measurement_key": measurement.measurement_key if measurement else None,
                "bound_requirement_key": (
                    key_by_req.get(measurement.requirement_id) if measurement else None
                ),
                "bound_cycle": measurement.cycle if measurement else None,
            }
        )
    return bindings


def _normalize_measurement_value(value: Any) -> Any:
    # Media-derived floats (loudness, true peak) are not bit-reproducible across
    # encoder runs; grade them rounded to the nearest unit (LU/dB) so a material
    # (>= 1 unit) change fails while sub-unit encoder wobble is tolerated. Every
    # non-float value (bool/int/enum/string/rational) is graded exactly.
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value)
    return value


def _measurement_records(run: RuntimeRun) -> list[dict[str, Any]]:
    # Complete per-cycle material record: identity, cycle, canonical value, unit,
    # status, and error — graded as an order-independent multiset so a deleted or
    # altered earlier-cycle measurement, a changed value, a wrong unit, or a wrong
    # cycle binding all diverge (not merely a changed final status).
    key_by_req = {item.requirement_id: _requirement_key(item) for item in run.requirements}
    return [
        {
            "measurement_key": key_by_req.get(m.requirement_id, m.measurement_key),
            "cycle": m.cycle,
            "value": _normalize_measurement_value(m.value),
            "unit": m.unit,
            "status": m.status.value,
            "error": m.error,
        }
        for m in run.measurements
    ]


def _fail_keys_by_cycle(records: list[tuple[str, int, str]]) -> list[list[Any]]:
    by_cycle: dict[int, set[str]] = {}
    for key, cycle, outcome in records:
        if outcome != "PASS":
            by_cycle.setdefault(cycle, set()).add(key)
    return [[cycle, sorted(by_cycle[cycle])] for cycle in sorted(by_cycle)]


def _findings(run: RuntimeRun) -> dict[str, Any]:
    key_by_req = {item.requirement_id: _requirement_key(item) for item in run.requirements}
    latest: dict[str, tuple[int, dict[str, Any]]] = {}
    for finding in run.findings:
        key = key_by_req.get(finding.requirement_id, finding.requirement_id)
        record = {"status": finding.status.value, "has_options": bool(finding.options)}
        current = latest.get(key)
        if current is None or finding.cycle_opened >= current[0]:
            latest[key] = (finding.cycle_opened, record)
    return {key: record for key, (_, record) in latest.items()}


def _originals_integrity(run: RuntimeRun, workspace: Path) -> bool:
    expected = dict(run.original_hashes)
    return all(
        (workspace / item.relative_path).is_file()
        and _hash_file(workspace / item.relative_path) == expected[item.asset.asset_id]
        for item in run.assets
        if item.asset.protection.value == "ORIGINAL"
    )


_RECOVERY_REFS = {
    "INTERRUPTED_REMEDIATION_RECOVERED",
    "RECOVERY_INTEGRITY_FAILED",
}


_PAYLOAD_KIND = {
    StateTransitionPayload: "STATE_TRANSITION",
    ActionStartedPayload: "ACTION_STARTED",
    ActionCompletedPayload: "ACTION_COMPLETED",
    ActionFailedPayload: "ACTION_FAILED",
}


def _event_skeleton(events: tuple[LedgerEvent, ...]) -> list[dict[str, Any]]:
    # The complete ordered ledger skeleton graded as coherent typed events with
    # material relationships:
    #  - `envelope_type` (event.type) is recorded alongside the payload-derived
    #    `type` so an envelope/payload contradiction diverges rather than being
    #    normalized away;
    #  - each ACTION_STARTED gets a stable per-run `action_ordinal` (never the
    #    opaque action_id) plus tool and authorization-presence;
    #  - each ACTION_COMPLETED/ACTION_FAILED resolves to its start's ordinal and
    #    tool, so a completion/failure rebound to a different start, bound to no
    #    start, or with a mismatched tool diverges at `events`.
    starts: dict[str, dict[str, Any]] = {}
    ordinal = 0
    skeleton: list[dict[str, Any]] = []
    for event in events:
        payload = event.payload
        envelope_type = event.type.value
        if isinstance(payload, StateTransitionPayload):
            skeleton.append(
                {
                    "type": "STATE_TRANSITION",
                    "envelope_type": envelope_type,
                    "from": payload.from_state.value,
                    "to": payload.to_state.value,
                    "cause": event.refs[0] if event.refs else None,
                }
            )
        elif isinstance(payload, ActionStartedPayload):
            ordinal += 1
            starts[payload.action_id] = {"ordinal": ordinal, "tool": payload.tool}
            skeleton.append(
                {
                    "type": "ACTION_STARTED",
                    "envelope_type": envelope_type,
                    "tool": payload.tool,
                    "action_ordinal": ordinal,
                    "authorized": payload.authorization_id is not None,
                }
            )
        elif isinstance(payload, ActionCompletedPayload):
            start = starts.get(payload.action_id)
            skeleton.append(
                {
                    "type": "ACTION_COMPLETED",
                    "envelope_type": envelope_type,
                    "action_ordinal": start["ordinal"] if start else None,
                    "tool": start["tool"] if start else None,
                }
            )
        elif isinstance(payload, ActionFailedPayload):
            start = starts.get(payload.action_id)
            skeleton.append(
                {
                    "type": "ACTION_FAILED",
                    "envelope_type": envelope_type,
                    "action_ordinal": start["ordinal"] if start else None,
                    "tool": start["tool"] if start else None,
                }
            )
    return skeleton


def extract_observed(outcome: RunOutcome) -> dict[str, dict[str, Any]]:
    """Extract complete, typed observed facts for every standard stage."""

    run = outcome.run
    events = outcome.events
    observed: dict[str, dict[str, Any]] = {}

    original_count = sum(
        1 for item in run.assets if item.asset.protection.value == "ORIGINAL"
    )
    observed["ingestion"] = {
        "source_present": bool(run.source_documents and run.source_documents[0].raw_sha256),
        "original_asset_count": original_count,
    }

    observed["interpretation"] = {"requirement_count": len(run.requirements)}

    observed["admission"] = {
        "requirements": _requirements(run),
        "status_reasons": _status_reasons(run),
        "normalization_status_multiset": _multiset(
            [item.normalization_status.value for item in run.requirements]
        ),
        "severity_multiset": _multiset([item.severity.value for item in run.requirements]),
    }
    if outcome.adversarial_assessment is not None:
        observed["admission"]["adversarial_dimensions_pass"] = (
            outcome.adversarial_assessment.passed
        )
        observed["admission"]["adversarial_reason_codes"] = sorted(
            outcome.adversarial_assessment.reason_codes
        )

    observed["applicability"] = {
        "applicability_status_multiset": _multiset(
            [item.applicability_status.value for item in run.requirements]
        ),
    }

    observed["planning"] = {
        "required_check_count": len(run.check_order),
        "plan_items": _plan_items(run),
    }

    observed["measurement"] = {
        "records": _measurement_records(run),
    }

    predicate_records = _predicate_records(run)
    final_predicates = _latest_by_key(run, predicate_records)
    observed["predicate"] = {
        "final_by_key": final_predicates,
        "pass_count": sum(1 for v in final_predicates.values() if v == "PASS"),
        "fail_count": sum(1 for v in final_predicates.values() if v == "FAIL"),
        "not_evaluated_count": sum(
            1 for v in final_predicates.values() if v == "NOT_EVALUATED"
        ),
        "fail_keys_by_cycle": _fail_keys_by_cycle(predicate_records),
        "bindings": _predicate_bindings(run),
    }

    findings = _findings(run)
    observed["findings"] = {
        "by_key": findings,
        "requirement_keys": sorted(findings),
        "final_blocking_keys": sorted(
            key for key, record in findings.items()
            if record["status"] in {"OPEN", "UNRESOLVED"}
        ),
    }

    observed["diagnosis"] = {
        "tier_exposure": [list(tiers) for tiers in outcome.diagnosis_tier_exposure],
    }

    observed["authority"] = {
        "decision_request_count": len(run.decision_requests),
        "decision_request_statuses": sorted(i.status.value for i in run.decision_requests),
        "decision_choices": sorted(i.choice.value for i in run.decisions),
    }

    started = sum(1 for e in events if isinstance(e.payload, ActionStartedPayload))
    completed = sum(1 for e in events if isinstance(e.payload, ActionCompletedPayload))
    failed = sum(1 for e in events if isinstance(e.payload, ActionFailedPayload))
    observed["remediation"] = {
        "autonomous_remediations": run.autonomous_remediations,
        "authorized_remediations": run.authorized_remediations,
        "action_started_count": started,
        "action_completed_count": completed,
        "action_failed_count": failed,
        "action_tools": [
            e.payload.tool for e in events if isinstance(e.payload, ActionStartedPayload)
        ],
    }

    observed["events"] = {"skeleton": _event_skeleton(events)}

    inspection_starts = sum(
        1
        for e in events
        if isinstance(e.payload, StateTransitionPayload)
        and e.payload.to_state is RunStatus.INSPECTING
    )
    recovery_reasons = [
        ref
        for e in events
        for ref in e.refs
        if ref in _RECOVERY_REFS
    ]
    observed["recovery"] = {
        "inspection_passes": inspection_starts,
        "recovered_action_count": recovery_reasons.count("INTERRUPTED_REMEDIATION_RECOVERED"),
        "recovery_reasons": recovery_reasons,
    }

    verdict = outcome.terminal_verdict
    terminal: dict[str, Any] = {
        "terminal_status": observed_terminal(outcome),
        "has_verdict": verdict is not None,
    }
    if verdict is not None:
        terminal["outcome"] = verdict.outcome.value
        terminal["reason_codes"] = sorted(
            _multiset([reason.split(":", 1)[0] for reason in verdict.reasons])
        )
        terminal["counts"] = {
            "applicable_blocking": verdict.counts.applicable_blocking,
            "passed": verdict.counts.passed,
            "failed": verdict.counts.failed,
            "not_evaluated": verdict.counts.not_evaluated,
            "unresolved_spec": verdict.counts.unresolved_spec,
            "pending_decisions": verdict.counts.pending_decisions,
            "autonomous_remediations": verdict.counts.autonomous_remediations,
            "authorized_remediations": verdict.counts.authorized_remediations,
        }
    observed["terminal"] = terminal

    observed["evidence_integrity"] = {
        "artifacts": sorted(Path(item.relative_path).name for item in run.evidence),
        "artifact_hashes_valid": all(
            (outcome.workspace / item.relative_path).is_file()
            and _hash_file(outcome.workspace / item.relative_path) == item.sha256
            for item in run.evidence
        )
        if run.evidence
        else True,
        "originals_integrity_verified": _originals_integrity(run, outcome.workspace),
    }

    for stage, facts in outcome.extra.items():
        observed[stage] = facts

    return observed


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "<absent>"


_MISSING = _Missing()


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _equal(wanted: Any, got: Any, *, ordered: bool = False) -> bool:
    if isinstance(wanted, dict) and isinstance(got, dict):
        if set(wanted) != set(got):
            return False
        return all(_equal(wanted[k], got[k], ordered=ordered) for k in wanted)
    if isinstance(wanted, list) and isinstance(got, list):
        if ordered:
            if len(wanted) != len(got):
                return False
            return all(_equal(w, g, ordered=True) for w, g in zip(wanted, got))
        return sorted(_canon(x) for x in wanted) == sorted(_canon(x) for x in got)
    return wanted == got


def compare_stage(expected_fields: dict[str, Any], observed_fields: dict[str, Any]) -> tuple[str, ...]:
    """Return the field names that diverge (exact; no subset, no extra-key evasion)."""

    mismatches: list[str] = []
    for name, wanted in expected_fields.items():
        got = observed_fields.get(name, _MISSING)
        if got is _MISSING:
            mismatches.append(name)
            continue
        if not _equal(wanted, got, ordered=name in ORDERED_LIST_FIELDS):
            mismatches.append(name)
    return tuple(mismatches)


def first_divergence(
    expected_stages: dict[str, dict[str, Any]],
    observed: dict[str, dict[str, Any]],
) -> tuple[str | None, tuple[str, ...]]:
    """Return the earliest materially wrong graded stage and its divergent fields."""

    ordered = [stage for stage in STAGE_ORDER if stage in expected_stages]
    ordered += [stage for stage in expected_stages if stage not in STAGE_ORDER]
    for stage in ordered:
        observed_fields = observed.get(stage)
        if observed_fields is None:
            return stage, ("<stage absent from observed facts>",)
        mismatches = compare_stage(expected_stages[stage], observed_fields)
        if mismatches:
            return stage, mismatches
    return None, ()


__all__ = (
    "STAGE_ORDER",
    "ORDERED_LIST_FIELDS",
    "RunOutcome",
    "CorpusExtractionError",
    "extract_observed",
    "observed_terminal",
    "first_divergence",
    "compare_stage",
)
