"""Bounded S2 proposal, validation, retry, and fallback tests."""

from __future__ import annotations

from datetime import UTC, datetime

from aircheck.agent.contracts import InventoryAsset, PackageInventory
from aircheck.domain.admission import CandidateRequirement, ScopeDraft, admit
from aircheck.domain.assets import Asset
from aircheck.domain.constraints import ConstraintDraft
from aircheck.domain.planning import PlanProposal, derive_required_checks
from aircheck.domain.source import ingest_source_document, segment_source_document
from aircheck.domain.types import (
    AssetProtection,
    AssetRole,
    NormalizationStatus,
    PlanSource,
    ProposedObligation,
    SourceKind,
)


class ScriptedModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def generate(self, prompt, output_model):
        self.prompts.append((prompt, output_model))
        return self.outputs.pop(0)


def _planning_inputs():
    document = ingest_source_document(
        doc_id="doc_s2",
        run_id="run_m3_s2",
        kind=SourceKind.SPEC,
        title="S2 fixture",
        raw=b"Audio sample rate MUST be 48 kHz.",
        ingested_at=datetime(2026, 9, 7, tzinfo=UTC),
    )
    segments = segment_source_document(document)
    candidate = CandidateRequirement(
        candidate_id="candidate_audio",
        segment_ids=(segments[0].segment_id,),
        quote=segments[0].text,
        proposed_obligation=ProposedObligation.MUST,
        measurement_key="audio.sample_rate",
        constraint_draft=ConstraintDraft(
            operator="EQUALS", raw_values=("48",), raw_unit="kHz"
        ),
        scope_draft=ScopeDraft(asset_role="PROGRAM_MASTER", stream="primary_audio"),
        proposed_status=NormalizationStatus.EXECUTABLE,
        reason="Exact sample-rate requirement.",
        confidence=1,
    )
    requirement = admit(candidate, document, segments)
    asset = Asset(
        asset_id="asset_master",
        run_id="run_m3_s2",
        role=AssetRole.PROGRAM_MASTER,
        filename="master.mov",
        sha256="a" * 64,
        size_bytes=100,
        protection=AssetProtection.WORKING,
    )
    checks = derive_required_checks((requirement,), (asset,))
    return (asset,), checks


def test_s2_retries_rejected_omission_then_accepts_complete_grouped_plan() -> None:
    from aircheck.agent.planning import construct_plan

    assets, checks = _planning_inputs()
    model = ScriptedModel(
        [
            {"ordered_items": [], "strategy_note": "omit"},
            {
                "ordered_items": [checks[0].check_id],
                "groups": [{"label": "Audio", "check_ids": [checks[0].check_id]}],
                "strategy_note": "Inspect audio structure first.",
            },
        ]
    )

    result = construct_plan(
        model,
        run_id="run_m3_s2",
        cycle=0,
        required_checks=checks,
        assets=assets,
    )

    assert result.attempts == 2
    assert result.plan.source is PlanSource.PROPOSAL
    assert result.plan.items[0].check_id == checks[0].check_id
    assert result.validation_failures == (
        "PLAN_REJECTED:every required check and extra must appear exactly once",
    )
    assert "PLAN_REJECTED" in model.prompts[1][0]


def test_s2_uses_complete_deterministic_fallback_after_second_rejection() -> None:
    from aircheck.agent.planning import construct_plan

    assets, checks = _planning_inputs()
    model = ScriptedModel([{"ordered_items": []}, {"ordered_items": []}])

    result = construct_plan(
        model,
        run_id="run_m3_s2",
        cycle=0,
        required_checks=checks,
        assets=assets,
    )

    assert result.attempts == 2
    assert result.plan.source is PlanSource.FALLBACK
    assert tuple(item.check_id for item in result.plan.items) == (checks[0].check_id,)
    assert result.validation_failures == (
        "PLAN_REJECTED:every required check and extra must appear exactly once",
        "PLAN_REJECTED:every required check and extra must appear exactly once",
    )


def test_s2_provider_failures_are_bounded_and_fall_back_without_provider_prose() -> None:
    from aircheck.agent.planning import construct_plan

    class FailingModel:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt, output_model):
            self.calls += 1
            raise RuntimeError("private provider prose")

    assets, checks = _planning_inputs()
    model = FailingModel()

    result = construct_plan(
        model,
        run_id="run_m3_s2",
        cycle=0,
        required_checks=checks,
        assets=assets,
    )

    assert model.calls == 2
    assert result.plan.source is PlanSource.FALLBACK
    assert result.validation_failures == ("PROVIDER_ERROR:RuntimeError",) * 2
