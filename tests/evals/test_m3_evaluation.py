"""M3 Northstar normalization/planning and adversarial ground-truth proof."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aircheck.agent.contracts import InventoryAsset, PackageInventory
from aircheck.agent.interpretation import interpret_requirements
from aircheck.domain.assets import Asset
from aircheck.domain.planning import (
    PlanProposal,
    deterministic_fallback_plan,
    derive_required_checks,
)
from aircheck.domain.source import ingest_source_document, segment_source_document
from aircheck.domain.types import AssetProtection, AssetRole, SourceKind
from tests.agent.fixtures import adversarial_batch, northstar_batch


ROOT = Path(__file__).parents[2]
PROFILE_ROOT = ROOT / "specifications" / "northstar"


class StaticModel:
    def __init__(self, output):
        self.output = output

    def generate(self, prompt, output_model):
        return self.output


def _inventory(run_id: str, profile_name: str) -> PackageInventory:
    return PackageInventory(
        run_id=run_id,
        content_type="program",
        destination_profile=profile_name,
        assets=(
            InventoryAsset(
                asset_id="asset_master", role="PROGRAM_MASTER", filename="source.mov"
            ),
            InventoryAsset(
                asset_id="asset_captions", role="CAPTIONS", filename="source.vtt"
            ),
            InventoryAsset(
                asset_id="asset_manifest", role="MANIFEST", filename="SHA256SUMS"
            ),
        ),
    )


def _assets(run_id: str) -> tuple[Asset, ...]:
    return tuple(
        Asset(
            asset_id=asset_id,
            run_id=run_id,
            role=role,
            filename=filename,
            sha256=character * 64,
            size_bytes=100,
            protection=AssetProtection.ORIGINAL,
        )
        for asset_id, role, filename, character in (
            ("asset_master", AssetRole.PROGRAM_MASTER, "source.mov", "a"),
            ("asset_captions", AssetRole.CAPTIONS, "source.vtt", "b"),
            ("asset_manifest", AssetRole.MANIFEST, "SHA256SUMS", "c"),
        )
    )


def _source(path: Path, *, doc_id: str, run_id: str, title: str):
    document = ingest_source_document(
        doc_id=doc_id,
        run_id=run_id,
        kind=SourceKind.SPEC,
        title=title,
        raw=path.read_bytes(),
        ingested_at=datetime(2026, 9, 7, tzinfo=UTC),
    )
    return document, segment_source_document(document)


def test_northstar_profiles_normalize_exactly_and_produce_distinct_complete_plans() -> None:
    from aircheck.agent.evaluation import evaluate_product_profile

    profiles = json.loads(
        (PROFILE_ROOT / "profiles" / "m2_product_profiles.json").read_text(
            encoding="utf-8"
        )
    )["profiles"]
    observed = {}
    for profile in profiles:
        profile_id = profile["profile_id"]
        run_id = "run_m3_northstar"
        document, segments = _source(
            PROFILE_ROOT / "source" / profile["source_file"],
            doc_id=f"doc_{profile_id.removesuffix('_v1')}",
            run_id=run_id,
            title=profile["profile_name"],
        )
        assert document.raw_sha256 == profile["source_sha256"]
        assert document.normalized_sha256 == profile["normalized_source_sha256"]
        interpretation = interpret_requirements(
            StaticModel(northstar_batch(profile_id, segments)),
            document,
            _inventory(run_id, profile["profile_name"]),
        )
        assessment = evaluate_product_profile(interpretation.requirements, profile)
        assert assessment.passed, assessment.errors
        checks = derive_required_checks(interpretation.requirements, _assets(run_id))
        plan = deterministic_fallback_plan(
            run_id=run_id,
            cycle=0,
            required_checks=checks,
            assets=_assets(run_id),
        )
        observed[profile_id] = (interpretation, checks, plan)

    broadcast = observed["northstar_broadcast_master_v1"]
    preview = observed["northstar_digital_preview_v1"]
    assert len(broadcast[1]) == 15
    assert len(preview[1]) == 13
    assert broadcast[2].plan_id != preview[2].plan_id
    assert {item.requirement_id for item in broadcast[2].items} != {
        item.requirement_id for item in preview[2].items
    }


def test_each_adversarial_spec_matches_unchanged_m2_typed_ground_truth() -> None:
    from aircheck.agent.evaluation import evaluate_adversarial_outcome

    expected_root = ROOT / "evals" / "expected"
    spec_root = ROOT / "evals" / "specs"
    expected_files = tuple(
        path for path in sorted(expected_root.glob("*.json")) if path.name != "README.json"
    )
    assert len(expected_files) == 7

    for expected_path in expected_files:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        fixture_id = expected["fixture_id"]
        run_id = f"run_m3_{fixture_id}"
        document, segments = _source(
            spec_root / f"{fixture_id}.md",
            doc_id=f"doc_m3_{fixture_id}",
            run_id=run_id,
            title=f"M3 {fixture_id}",
        )
        interpretation = interpret_requirements(
            StaticModel(adversarial_batch(fixture_id, segments)),
            document,
            _inventory(run_id, "Adversarial evaluation"),
        )

        assessment = evaluate_adversarial_outcome(
            interpretation.requirements,
            expected,
        )

        assert assessment.passed, (fixture_id, assessment.errors)
        assert assessment.terminal_evaluated is False


def test_injected_instruction_cannot_become_a_check_or_plan_action() -> None:
    from aircheck.agent.evaluation import evaluate_adversarial_outcome

    fixture_id = "injected_instruction"
    run_id = "run_m3_injected_instruction"
    document, segments = _source(
        ROOT / "evals" / "specs" / f"{fixture_id}.md",
        doc_id="doc_m3_injected_instruction",
        run_id=run_id,
        title="M3 injected instruction",
    )
    interpretation = interpret_requirements(
        StaticModel(adversarial_batch(fixture_id, segments)),
        document,
        _inventory(run_id, "Adversarial evaluation"),
    )
    checks = derive_required_checks(interpretation.requirements, _assets(run_id))
    plan = deterministic_fallback_plan(
        run_id=run_id, cycle=0, required_checks=checks, assets=_assets(run_id)
    )

    assert checks == ()
    assert plan.items == ()
    assert evaluate_adversarial_outcome(
        interpretation.requirements,
        json.loads(
            (ROOT / "evals" / "expected" / f"{fixture_id}.json").read_text(
                encoding="utf-8"
            )
        ),
    ).passed


def test_bounded_runner_keeps_inputs_separate_and_emits_only_typed_safe_results(
    tmp_path: Path,
) -> None:
    from evals.runners.m3 import run_evaluation, write_receipt

    profile_data = json.loads(
        (PROFILE_ROOT / "profiles" / "m2_product_profiles.json").read_text(
            encoding="utf-8"
        )
    )["profiles"]
    candidate_outputs = []
    for profile in profile_data:
        _, segments = _source(
            PROFILE_ROOT / "source" / profile["source_file"],
            doc_id=f"doc_{profile['profile_id'].removesuffix('_v1')}",
            run_id=f"run_m3_{profile['profile_id'].removeprefix('northstar_').removesuffix('_v1')}",
            title=profile["profile_name"],
        )
        candidate_outputs.extend([northstar_batch(profile["profile_id"], segments)] * 3)
    for expected_path in sorted((ROOT / "evals" / "expected").glob("*.json")):
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        fixture_id = expected["fixture_id"]
        _, segments = _source(
            ROOT / "evals" / "specs" / f"{fixture_id}.md",
            doc_id=f"doc_m3_{fixture_id}",
            run_id=f"run_m3_{fixture_id}",
            title=f"M3 {fixture_id}",
        )
        candidate_outputs.append(adversarial_batch(fixture_id, segments))

    class QueueModel:
        def __init__(self, outputs):
            self.outputs = list(outputs)
            self.candidate_calls = 0
            self.plan_calls = 0

        def generate(self, prompt, output_model):
            if output_model is PlanProposal:
                self.plan_calls += 1
                check_ids = tuple(dict.fromkeys(re.findall(r"check_[0-9a-f]{16}", prompt)))
                return {
                    "ordered_items": check_ids,
                    "strategy_note": "Keep deterministic check order.",
                }
            self.candidate_calls += 1
            return self.outputs.pop(0)

    model = QueueModel(candidate_outputs)
    receipt = run_evaluation(model, profile_runs=3)

    assert receipt["accepted"] is True
    assert model.candidate_calls == 13
    assert model.plan_calls == 2
    assert len(receipt["product_profiles"]) == 2
    assert all(len(profile["runs"]) == 3 for profile in receipt["product_profiles"])
    assert len(receipt["adversarial_inputs"]) == 7
    assert {item["fixture_id"] for item in receipt["adversarial_inputs"]} == {
        path.stem for path in (ROOT / "evals" / "expected").glob("*.json")
    }
    rendered = json.dumps(receipt, sort_keys=True)
    assert "provider_response" not in rendered
    assert "private provider prose" not in rendered
    assert "reasoningSignature" not in rendered

    with pytest.raises(ValueError, match="docs/evidence/M3"):
        write_receipt(receipt, tmp_path / "outside.json")
