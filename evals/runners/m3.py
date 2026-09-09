"""Bounded live M3 semantic evaluation over the frozen M2 universe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aircheck.agent.contracts import InventoryAsset, PackageInventory, StructuredSemanticModel
from aircheck.agent.evaluation import evaluate_adversarial_outcome, evaluate_product_profile
from aircheck.agent.interpretation import interpret_requirements
from aircheck.agent.planning import construct_plan
from aircheck.agent.provider import Gemini25FlashModel
from aircheck.domain.admission import Requirement
from aircheck.domain.assets import Asset
from aircheck.domain.planning import derive_required_checks
from aircheck.domain.source import ingest_source_document
from aircheck.domain.types import AssetProtection, AssetRole, SourceKind


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = ROOT / "docs" / "evidence" / "M3"
PROFILE_PATH = ROOT / "specifications" / "northstar" / "profiles" / "m2_product_profiles.json"
SOURCE_ROOT = ROOT / "specifications" / "northstar" / "source"
EXPECTED_ROOT = ROOT / "evals" / "expected"
SPEC_ROOT = ROOT / "evals" / "specs"
HASH_PATH = ROOT / "synthetic" / "source" / "m2_expected_hashes.json"


def _source_document(
    path: Path,
    *,
    doc_id: str,
    run_id: str,
    title: str,
):
    return ingest_source_document(
        doc_id=doc_id,
        run_id=run_id,
        kind=SourceKind.SPEC,
        title=title,
        raw=path.read_bytes(),
        ingested_at=datetime(2026, 9, 7, tzinfo=UTC),
    )


def _asset_rows() -> tuple[tuple[str, AssetRole, str], ...]:
    return (
        ("asset_clean_master", AssetRole.PROGRAM_MASTER, "THE_LAST_LIGHTKEEPER_NSBM_v1.mov"),
        ("asset_clean_captions", AssetRole.CAPTIONS, "THE_LAST_LIGHTKEEPER_NSBM_v1.vtt"),
        ("asset_clean_manifest", AssetRole.MANIFEST, "SHA256SUMS"),
    )


def _inventory(run_id: str, profile_name: str) -> PackageInventory:
    return PackageInventory(
        run_id=run_id,
        content_type="program",
        destination_profile=profile_name,
        assets=tuple(
            InventoryAsset(asset_id=asset_id, role=role, filename=filename)
            for asset_id, role, filename in _asset_rows()
        ),
    )


def _assets(run_id: str, hashes: dict[str, str]) -> tuple[Asset, ...]:
    return tuple(
        Asset(
            asset_id=asset_id,
            run_id=run_id,
            role=role,
            filename=filename,
            sha256=hashes[filename],
            size_bytes=0,
            protection=AssetProtection.ORIGINAL,
        )
        for asset_id, role, filename in _asset_rows()
    )


def _requirement_summary(requirement: Requirement) -> dict[str, Any]:
    return {
        "requirement_id": requirement.requirement_id,
        "segment_ids": list(requirement.span.segment_ids),
        "normalization_status": requirement.normalization_status.value,
        "status_reasons": list(requirement.status_reasons),
        "severity": requirement.severity.value,
        "severity_source": requirement.severity_source,
        "applicability_status": requirement.applicability_status.value,
        "applicability": [
            condition.model_dump(mode="json") for condition in requirement.applicability
        ],
        "constraint": requirement.constraint.model_dump(mode="json")
        if requirement.constraint
        else None,
        "verification": requirement.verification.model_dump(mode="json")
        if requirement.verification
        else None,
        "automation_disposition": requirement.automation_disposition.value,
        "rendered_text": requirement.rendered_text,
        "candidate_ref": requirement.candidate_ref,
    }


def _constraint_fingerprint(requirements: tuple[Requirement, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            json.dumps(
                requirement.constraint.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
            for requirement in requirements
            if requirement.constraint is not None
        )
    )


def run_evaluation(
    model: StructuredSemanticModel,
    *,
    profile_runs: int = 3,
) -> dict[str, Any]:
    """Run the exact M3 proof matrix without merging independent source inputs."""

    if profile_runs != 3:
        raise ValueError("M3 requires exactly three runs per valid product profile")
    profile_data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))["profiles"]
    hash_record = json.loads(HASH_PATH.read_text(encoding="utf-8"))
    clean_hashes = hash_record["clean_original_hashes"]
    product_receipts: list[dict[str, Any]] = []
    profile_fingerprints: dict[str, tuple[str, ...]] = {}
    semantic_calls = 0

    for profile in profile_data:
        profile_id = profile["profile_id"]
        short_id = profile_id.removeprefix("northstar_").removesuffix("_v1")
        run_id = f"run_m3_{short_id}"
        document = _source_document(
            SOURCE_ROOT / profile["source_file"],
            doc_id=f"doc_{profile_id.removesuffix('_v1')}",
            run_id=run_id,
            title=profile["profile_name"],
        )
        inventory = _inventory(run_id, profile["profile_name"])
        run_receipts = []
        last_requirements: tuple[Requirement, ...] = ()
        for run_number in range(1, profile_runs + 1):
            interpretation = interpret_requirements(model, document, inventory)
            semantic_calls += interpretation.attempts
            assessment = evaluate_product_profile(interpretation.requirements, profile)
            last_requirements = interpretation.requirements
            run_receipts.append(
                {
                    "run": run_number,
                    "attempts": interpretation.attempts,
                    "validation_failures": list(interpretation.validation_failures),
                    "candidate_count": len(interpretation.candidates),
                    "requirements": [
                        _requirement_summary(requirement)
                        for requirement in interpretation.requirements
                    ],
                    "accepted": assessment.passed,
                    "errors": list(assessment.errors),
                }
            )

        assets = _assets(run_id, clean_hashes)
        required_checks = derive_required_checks(last_requirements, assets)
        planning = construct_plan(
            model,
            run_id=run_id,
            cycle=0,
            required_checks=required_checks,
            assets=assets,
        )
        semantic_calls += planning.attempts
        profile_fingerprints[profile_id] = _constraint_fingerprint(last_requirements)
        product_receipts.append(
            {
                "profile_id": profile_id,
                "profile_name": profile["profile_name"],
                "source_sha256": document.raw_sha256,
                "normalized_source_sha256": document.normalized_sha256,
                "runs": run_receipts,
                "plan": {
                    "plan_id": planning.plan.plan_id,
                    "source": planning.plan.source.value,
                    "attempts": planning.attempts,
                    "validation_failures": list(planning.validation_failures),
                    "required_check_count": len(required_checks),
                    "items": [
                        {
                            "check_id": item.check_id,
                            "requirement_id": item.requirement_id,
                            "asset_id": item.asset_id,
                            "tool": item.tool,
                            "order": item.order,
                        }
                        for item in planning.plan.items
                    ],
                },
            }
        )

    adversarial_receipts = []
    for expected_path in sorted(EXPECTED_ROOT.glob("*.json")):
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        fixture_id = expected["fixture_id"]
        run_id = f"run_m3_{fixture_id}"
        document = _source_document(
            SPEC_ROOT / f"{fixture_id}.md",
            doc_id=f"doc_m3_{fixture_id}",
            run_id=run_id,
            title=f"M3 {fixture_id}",
        )
        interpretation = interpret_requirements(
            model,
            document,
            _inventory(run_id, "Adversarial evaluation"),
        )
        semantic_calls += interpretation.attempts
        assessment = evaluate_adversarial_outcome(
            interpretation.requirements,
            expected,
        )
        adversarial_receipts.append(
            {
                "fixture_id": fixture_id,
                "source_sha256": document.raw_sha256,
                "normalized_source_sha256": document.normalized_sha256,
                "attempts": interpretation.attempts,
                "validation_failures": list(interpretation.validation_failures),
                "candidate_count": len(interpretation.candidates),
                "requirements": [
                    _requirement_summary(requirement)
                    for requirement in interpretation.requirements
                ],
                "observed_reason_codes": list(assessment.reason_codes),
                "terminal_evaluated": assessment.terminal_evaluated,
                "accepted": assessment.passed,
                "errors": list(assessment.errors),
            }
        )

    distinct_product_plans = len(set(profile_fingerprints.values())) == len(
        profile_fingerprints
    )
    accepted = (
        distinct_product_plans
        and all(
            run["accepted"]
            for product in product_receipts
            for run in product["runs"]
        )
        and all(item["accepted"] for item in adversarial_receipts)
        and all(
            product["plan"]["required_check_count"] == len(product["plan"]["items"])
            for product in product_receipts
        )
    )
    return {
        "gate": "M3",
        "provider": {
            "path": "strands.models.gemini.GeminiModel",
            "model_id": "gemini-2.5-flash",
            "vertex_ai": True,
            "temperature": 0,
        },
        "profile_runs": profile_runs,
        "semantic_calls": semantic_calls,
        "source_package": {
            "fixture": "The Last Lightkeeper",
            "hashes": clean_hashes,
        },
        "product_profiles": product_receipts,
        "distinct_product_plans": distinct_product_plans,
        "adversarial_inputs": adversarial_receipts,
        "terminal_behavior": "NOT_EVALUATED_M3",
        "accepted": accepted,
    }


def write_receipt(receipt: dict[str, Any], output: Path) -> str:
    evidence_root = EVIDENCE_ROOT.resolve()
    resolved = output.resolve()
    if resolved.parent != evidence_root:
        raise ValueError("output must be a direct child of docs/evidence/M3")
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(rendered, encoding="utf-8", newline="\n")
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the bounded AIRCheck M3 interpretation/planning evaluation."
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    provider = Gemini25FlashModel(
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    )
    receipt = run_evaluation(provider, profile_runs=3)
    digest = write_receipt(receipt, args.output)
    print(f"receipt_path={args.output.as_posix()}")
    print(f"receipt_sha256={digest}")
    print(f"accepted={receipt['accepted']}")
    return 0 if receipt["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
