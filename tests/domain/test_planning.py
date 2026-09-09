"""R2 foundation invariants for complete, typed QC plans."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aircheck.domain import planning as planning_module
from aircheck.domain.admission import CandidateRequirement, ScopeDraft, admit
from aircheck.domain.assets import Asset
from aircheck.domain.constraints import ConstraintDraft
from aircheck.domain.planning import (
    ExtraCheck,
    PlanGroup,
    PlanProposal,
    PlanValidationError,
    ToolArgument,
    derive_required_checks,
    validate_plan,
)
from aircheck.domain.source import ingest_source_document, segment_source_document
from aircheck.domain.types import (
    AssetProtection,
    AssetRole,
    NormalizationStatus,
    PlanSource,
    ProposedObligation,
    SourceKind,
)


def _requirement(
    *,
    doc_id: str,
    text: str,
    measurement_key: str,
    operator: str,
    values: tuple[str, ...],
    unit: str | None,
    role: str,
    stream: str | None = None,
):
    document = ingest_source_document(
        doc_id=doc_id,
        run_id="run_plan_001",
        kind=SourceKind.SPEC,
        title="Northstar",
        raw=text.encode(),
        ingested_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    segments = segment_source_document(document)
    candidate = CandidateRequirement(
        candidate_id=f"candidate_{doc_id.removeprefix('doc_')}",
        segment_ids=(segments[0].segment_id,),
        quote=segments[0].text,
        proposed_obligation=ProposedObligation.MUST,
        measurement_key=measurement_key,
        constraint_draft=ConstraintDraft(
            operator=operator,
            raw_values=values,
            raw_unit=unit,
        ),
        scope_draft=ScopeDraft(asset_role=role, stream=stream),
        proposed_status=NormalizationStatus.EXECUTABLE,
        reason="fixture",
        confidence=1,
    )
    return admit(candidate, document, segments)


def _asset(asset_id: str, role: AssetRole) -> Asset:
    return Asset(
        asset_id=asset_id,
        run_id="run_plan_001",
        role=role,
        filename=f"{asset_id}.bin",
        sha256="a" * 64,
        size_bytes=100,
        protection=AssetProtection.WORKING,
    )


def test_required_check_set_covers_each_applicable_requirement_asset_pair_once() -> None:
    requirements = (
        _requirement(
            doc_id="doc_audio",
            text="Audio sample rate must be 48 kHz.",
            measurement_key="audio.sample_rate",
            operator="EQUALS",
            values=("48",),
            unit="kHz",
            role="PROGRAM_MASTER",
        ),
        _requirement(
            doc_id="doc_captions",
            text="Captions must be present.",
            measurement_key="captions.present",
            operator="PRESENT",
            values=(),
            unit=None,
            role="CAPTIONS",
        ),
    )
    assets = (
        _asset("asset_master", AssetRole.PROGRAM_MASTER),
        _asset("asset_captions", AssetRole.CAPTIONS),
    )

    checks = derive_required_checks(requirements, assets)

    assert len(checks) == 2
    assert {(check.requirement_id, check.asset_id) for check in checks} == {
        (requirements[0].requirement_id, "asset_master"),
        (requirements[1].requirement_id, "asset_captions"),
    }
    assert len({check.check_id for check in checks}) == 2


def test_plan_validator_rejects_omitted_required_check() -> None:
    requirement = _requirement(
        doc_id="doc_audio",
        text="Audio sample rate must be 48 kHz.",
        measurement_key="audio.sample_rate",
        operator="EQUALS",
        values=("48",),
        unit="kHz",
        role="PROGRAM_MASTER",
    )
    assets = (_asset("asset_master", AssetRole.PROGRAM_MASTER),)
    checks = derive_required_checks((requirement,), assets)

    with pytest.raises(PlanValidationError, match="exactly once"):
        validate_plan(
            run_id="run_plan_001",
            cycle=0,
            proposal=PlanProposal(ordered_items=(), strategy_note="omit everything"),
            required_checks=checks,
            assets=assets,
        )


def test_validated_plan_contains_every_check_and_only_tier_zero_extras() -> None:
    requirement = _requirement(
        doc_id="doc_audio",
        text="Audio sample rate must be 48 kHz.",
        measurement_key="audio.sample_rate",
        operator="EQUALS",
        values=("48",),
        unit="kHz",
        role="PROGRAM_MASTER",
    )
    assets = (_asset("asset_master", AssetRole.PROGRAM_MASTER),)
    checks = derive_required_checks((requirement,), assets)
    extra = ExtraCheck(
        item_id="item_extra_scan",
        tool="scan_package",
        asset_id="asset_master",
        rationale="Confirm package inventory before the required sample-rate check.",
        motivated_by=requirement.requirement_id,
    )
    proposal = PlanProposal(
        ordered_items=(extra.item_id, checks[0].check_id),
        extras=(extra,),
        strategy_note="Cheap structural probe first.",
    )

    plan = validate_plan(
        run_id="run_plan_001",
        cycle=0,
        proposal=proposal,
        required_checks=checks,
        assets=assets,
    )

    assert tuple(item.check_id for item in plan.items if item.check_id) == (
        checks[0].check_id,
    )
    assert plan.items[0].requirement_id is None
    assert plan.items[1].requirement_id == requirement.requirement_id


def test_plan_validator_rejects_duplicate_operation_with_implicit_identity_args() -> None:
    requirement = _requirement(
        doc_id="doc_audio",
        text="Audio sample rate must be 48 kHz.",
        measurement_key="audio.sample_rate",
        operator="EQUALS",
        values=("48",),
        unit="kHz",
        role="PROGRAM_MASTER",
    )
    assets = (_asset("asset_master", AssetRole.PROGRAM_MASTER),)
    checks = derive_required_checks((requirement,), assets)
    extra = ExtraCheck(
        item_id="item_extra_probe",
        tool="probe_media",
        asset_id="asset_master",
        rationale="Duplicate the required probe.",
        motivated_by=requirement.requirement_id,
    )

    with pytest.raises(PlanValidationError, match="duplicate"):
        validate_plan(
            run_id="run_plan_001",
            cycle=0,
            proposal=PlanProposal(
                ordered_items=(checks[0].check_id, extra.item_id),
                extras=(extra,),
            ),
            required_checks=checks,
            assets=assets,
        )


def test_plan_validator_rejects_mutating_extra_check() -> None:
    requirement = _requirement(
        doc_id="doc_audio",
        text="Audio sample rate must be 48 kHz.",
        measurement_key="audio.sample_rate",
        operator="EQUALS",
        values=("48",),
        unit="kHz",
        role="PROGRAM_MASTER",
    )
    assets = (_asset("asset_master", AssetRole.PROGRAM_MASTER),)
    checks = derive_required_checks((requirement,), assets)
    extra = ExtraCheck(
        item_id="item_extra_mutation",
        tool="rename_delivery_copy",
        asset_id="asset_master",
        rationale="This must not be admitted as an inspection extra.",
        motivated_by=requirement.requirement_id,
    )

    with pytest.raises(PlanValidationError, match="Tier-0"):
        validate_plan(
            run_id="run_plan_001",
            cycle=0,
            proposal=PlanProposal(
                ordered_items=(checks[0].check_id, extra.item_id),
                extras=(extra,),
            ),
            required_checks=checks,
            assets=assets,
        )


def test_required_checks_use_registered_tool_input_identifiers() -> None:
    loudness = _requirement(
        doc_id="doc_loudness",
        text="Integrated loudness must be between -26 and -22 LUFS.",
        measurement_key="audio.integrated_loudness",
        operator="RANGE",
        values=("-26", "-22"),
        unit="LUFS",
        role="PROGRAM_MASTER",
        stream="primary_audio",
    )
    manifest = _requirement(
        doc_id="doc_manifest",
        text="A checksum manifest must be present.",
        measurement_key="package.manifest_present",
        operator="PRESENT",
        values=(),
        unit=None,
        role="MANIFEST",
    )
    assets = (
        _asset("asset_master", AssetRole.PROGRAM_MASTER),
        _asset("asset_manifest", AssetRole.MANIFEST),
    )

    checks = derive_required_checks((loudness, manifest), assets)
    by_tool = {check.tool: check for check in checks}

    assert by_tool["measure_loudness"].tool_args == (
        ToolArgument(name="asset_id", value="asset_master"),
        ToolArgument(name="stream", value="primary_audio"),
    )
    assert by_tool["scan_package"].tool_args == (
        ToolArgument(name="run_id", value="run_plan_001"),
    )


def test_plan_validator_rejects_unknown_group_membership() -> None:
    requirement = _requirement(
        doc_id="doc_audio",
        text="Audio sample rate must be 48 kHz.",
        measurement_key="audio.sample_rate",
        operator="EQUALS",
        values=("48",),
        unit="kHz",
        role="PROGRAM_MASTER",
    )
    assets = (_asset("asset_master", AssetRole.PROGRAM_MASTER),)
    checks = derive_required_checks((requirement,), assets)

    with pytest.raises(PlanValidationError, match="group"):
        validate_plan(
            run_id="run_plan_001",
            cycle=0,
            proposal=PlanProposal(
                ordered_items=(checks[0].check_id,),
                groups=(PlanGroup(label="Audio", check_ids=("check_unknown",)),),
            ),
            required_checks=checks,
            assets=assets,
        )


def test_plan_validator_rejects_invalid_extra_tool_arguments() -> None:
    requirement = _requirement(
        doc_id="doc_audio",
        text="Audio sample rate must be 48 kHz.",
        measurement_key="audio.sample_rate",
        operator="EQUALS",
        values=("48",),
        unit="kHz",
        role="PROGRAM_MASTER",
    )
    assets = (_asset("asset_master", AssetRole.PROGRAM_MASTER),)
    checks = derive_required_checks((requirement,), assets)
    extra = ExtraCheck(
        item_id="item_extra_loudness",
        tool="measure_loudness",
        asset_id="asset_master",
        tool_args=(ToolArgument(name="stream", value="C:/source/master.mov"),),
        rationale="Measure a second audio characteristic.",
        motivated_by=requirement.requirement_id,
    )

    with pytest.raises(PlanValidationError, match="arguments"):
        validate_plan(
            run_id="run_plan_001",
            cycle=0,
            proposal=PlanProposal(
                ordered_items=(checks[0].check_id, extra.item_id),
                extras=(extra,),
            ),
            required_checks=checks,
            assets=assets,
        )


def test_deterministic_fallback_is_stable_and_complete() -> None:
    requirements = (
        _requirement(
            doc_id="doc_audio",
            text="Audio sample rate must be 48 kHz.",
            measurement_key="audio.sample_rate",
            operator="EQUALS",
            values=("48",),
            unit="kHz",
            role="PROGRAM_MASTER",
        ),
        _requirement(
            doc_id="doc_captions",
            text="Captions must be present.",
            measurement_key="captions.present",
            operator="PRESENT",
            values=(),
            unit=None,
            role="CAPTIONS",
        ),
    )
    assets = (
        _asset("asset_master", AssetRole.PROGRAM_MASTER),
        _asset("asset_captions", AssetRole.CAPTIONS),
    )
    checks = derive_required_checks(requirements, assets)

    assert hasattr(planning_module, "deterministic_fallback_plan")
    deterministic_fallback_plan = planning_module.deterministic_fallback_plan
    first = deterministic_fallback_plan(
        run_id="run_plan_001",
        cycle=0,
        required_checks=tuple(reversed(checks)),
        assets=assets,
    )
    second = deterministic_fallback_plan(
        run_id="run_plan_001",
        cycle=0,
        required_checks=checks,
        assets=tuple(reversed(assets)),
    )

    assert first == second
    assert first.source is PlanSource.FALLBACK
    assert {item.check_id for item in first.items} == {
        check.check_id for check in checks
    }
    assert first.strategy_note == "Deterministic required-check fallback."
