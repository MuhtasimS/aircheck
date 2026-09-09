"""Behavioral tests for AIRCheck's canonical domain boundary."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from aircheck.domain.models import DeliveryRun, Event, EventType
from aircheck.domain.status import RunStatus


def test_delivery_run_validates_canonical_fixture(
    canonical_run_data: dict[str, object],
) -> None:
    """Breaking the agreed object shape must reject real-looking run data."""
    run = DeliveryRun.model_validate(canonical_run_data)

    assert run.status is RunStatus.AWAITING_HUMAN_DECISION
    assert run.requirements[0].source_reference == "Northstar Broadcast Master §4.2"
    assert run.events[-1].event_type is EventType.HUMAN_DECISION_REQUIRED
    assert run.working_assets[0].derived_from == "asset_captions_en"


def test_delivery_run_rejects_duplicate_requirement_ids(
    canonical_run_data: dict[str, object],
) -> None:
    """Losing identifier uniqueness would make findings ambiguous."""
    requirements = canonical_run_data["requirements"]
    assert isinstance(requirements, list)
    requirements.append(dict(requirements[0]))

    with pytest.raises(ValidationError, match="duplicate requirement_id"):
        DeliveryRun.model_validate(canonical_run_data)


def test_delivery_run_rejects_event_for_another_run(
    canonical_run_data: dict[str, object],
) -> None:
    """Accepting a foreign ledger event would break run auditability."""
    events = canonical_run_data["events"]
    assert isinstance(events, list)
    events[0]["run_id"] = "run_other"

    with pytest.raises(ValidationError, match="event run_id"):
        DeliveryRun.model_validate(canonical_run_data)


def test_ready_run_requires_complete_final_evidence(
    canonical_run_data: dict[str, object],
) -> None:
    """A ready state without earned evidence must never validate."""
    canonical_run_data["status"] = RunStatus.DELIVERY_READY
    canonical_run_data["pending_decision"] = None
    canonical_run_data["final_evidence"] = None

    with pytest.raises(ValidationError, match="final_evidence"):
        DeliveryRun.model_validate(canonical_run_data)


def test_ready_run_rejects_unresolved_blockers(
    canonical_run_data: dict[str, object],
) -> None:
    """A green terminal state must not coexist with unresolved blockers."""
    canonical_run_data["status"] = RunStatus.DELIVERY_READY
    canonical_run_data["pending_decision"] = None
    canonical_run_data["final_evidence"] = {
        "total_requirements": 3,
        "verified_requirements": 3,
        "resolved_findings": 2,
        "autonomous_remediations": 1,
        "authorized_remediations": 1,
        "unresolved_blockers": 1,
        "artifacts": [],
    }

    with pytest.raises(ValidationError, match="unresolved blockers"):
        DeliveryRun.model_validate(canonical_run_data)


def test_pending_decision_requires_matching_run_state(
    canonical_run_data: dict[str, object],
) -> None:
    """A hidden approval pause must not survive outside its explicit state."""
    canonical_run_data["status"] = RunStatus.REMEDIATING

    with pytest.raises(ValidationError, match="pending_decision"):
        DeliveryRun.model_validate(canonical_run_data)


def test_finding_references_must_resolve_inside_run(
    canonical_run_data: dict[str, object],
) -> None:
    """Dangling finding references would make evidence unverifiable."""
    findings = canonical_run_data["findings"]
    assert isinstance(findings, list)
    findings[0]["requirement_id"] = "req_missing"

    with pytest.raises(ValidationError, match="unknown requirement_id"):
        DeliveryRun.model_validate(canonical_run_data)


def test_events_must_remain_chronological(
    canonical_run_data: dict[str, object],
) -> None:
    """Reordering causal events must invalidate the ledger."""
    events = canonical_run_data["events"]
    assert isinstance(events, list)
    events[1]["timestamp"] = "2026-09-03T18:50:00Z"

    with pytest.raises(ValidationError, match="chronological"):
        DeliveryRun.model_validate(canonical_run_data)


def test_event_timestamps_are_timezone_aware() -> None:
    """Naive timestamps cannot establish a trustworthy sequence."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        Event(
            event_id="evt_001",
            run_id="run_001",
            timestamp=datetime(2026, 9, 3, 12, 0),
            event_type=EventType.SPEC_INTERPRETED,
            actor="AIRCHECK",
            summary="Specification normalized.",
        )


def test_unknown_fields_are_rejected(
    canonical_run_data: dict[str, object],
) -> None:
    """Silently accepting invented schema fields would weaken the contract."""
    canonical_run_data["agent_opinion"] = "looks fine"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DeliveryRun.model_validate(canonical_run_data)
