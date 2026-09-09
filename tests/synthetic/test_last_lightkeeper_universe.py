"""M2 synthetic-universe behavior tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]


def test_universe_generation_is_reproducible_and_preserves_clean_source(
    tmp_path: Path,
) -> None:
    """Changing generation inputs or mutating a clean original must fail this test."""

    from synthetic.generator.last_lightkeeper import build_universe, snapshot_hashes

    first = build_universe(tmp_path / "first")
    first_clean_before = snapshot_hashes(first.clean.originals)
    second = build_universe(tmp_path / "second")

    assert snapshot_hashes(first.clean.originals) == first_clean_before
    assert snapshot_hashes(first.root) == snapshot_hashes(second.root)
    assert first.clean.fixture_id == "the_last_lightkeeper_clean_broadcast_v1"
    assert first.hero.fixture_id == "the_last_lightkeeper_hero_faults_v1"
    assert first.hero.source_fixture_id == first.clean.fixture_id
    assert first.hero.source_asset_hashes == first_clean_before


def test_generated_asset_hashes_match_the_frozen_m2_fixture_record(
    tmp_path: Path,
) -> None:
    """Changing deterministic fixture bytes without updating the record must fail."""

    from synthetic.generator.last_lightkeeper import build_universe, snapshot_hashes

    expected = json.loads(
        (ROOT / "synthetic" / "source" / "m2_expected_hashes.json").read_text(
            encoding="utf-8"
        )
    )
    universe = build_universe(tmp_path / "universe")

    assert snapshot_hashes(universe.clean.originals) == expected["clean_original_hashes"]
    assert snapshot_hashes(universe.hero.originals) == expected["hero_original_hashes"]


def test_m1_inspection_proves_clean_and_planted_hero_facts(tmp_path: Path) -> None:
    """Removing a fault or changing a M1 fact must fail this test."""

    from synthetic.generator.last_lightkeeper import build_universe, inspect_package

    universe = build_universe(tmp_path / "universe")
    clean = inspect_package(universe.clean)
    hero = inspect_package(universe.hero)

    assert clean["scan_package"].status.value == "OK"
    assert clean["probe_media"].status.value == "OK"
    assert clean["measure_loudness"].status.value == "OK"
    assert clean["inspect_captions"].status.value == "OK"
    assert clean["verify_manifest"].status.value == "OK"
    assert _values(clean["scan_package"])[(None, "package.manifest_present")] is True
    assert _values(clean["probe_media"]) == {
        ("asset_clean_master", "container.format"): "quicktime mov",
        ("asset_clean_master", "video.codec"): "h264",
        ("asset_clean_master", "video.width"): 1920,
        ("asset_clean_master", "video.height"): 1080,
        ("asset_clean_master", "video.frame_rate"): "24000/1001",
        ("asset_clean_master", "video.scan_type"): "progressive",
        ("asset_clean_master", "audio.channel_count"): 2,
        ("asset_clean_master", "audio.channel_layout"): "stereo",
        ("asset_clean_master", "audio.sample_rate"): 48000,
    }
    assert _values(clean["inspect_captions"])[
        ("asset_clean_captions", "captions.format")
    ] == "webvtt"
    assert _values(clean["measure_loudness"])[
        ("asset_clean_master", "audio.integrated_loudness")
    ] == pytest.approx(-24.0, abs=0.25)

    assert hero["scan_package"].status.value == "OK"
    assert hero["probe_media"].status.value == "OK"
    assert hero["measure_loudness"].status.value == "OK"
    assert hero["inspect_captions"].status.value == "OK"
    assert hero["verify_manifest"].status.value == "ERROR"
    assert hero["verify_manifest"].error == "MANIFEST_ABSENT"
    assert _values(hero["scan_package"])[(None, "package.manifest_present")] is False
    assert _values(hero["probe_media"])[
        ("asset_hero_master", "video.codec")
    ] == "h264"
    assert _values(hero["scan_package"])[
        ("asset_hero_master", "package.filename[role]")
    ] == "the-last-lightkeeper-delivery-v1.mov"
    assert _values(hero["inspect_captions"])[
        ("asset_hero_captions", "captions.format")
    ] == "srt"
    assert _values(hero["measure_loudness"])[
        ("asset_hero_master", "audio.integrated_loudness")
    ] == pytest.approx(-19.0, abs=0.25)


def test_fault_ledger_distinguishes_intentional_injections_from_generation_errors(
    tmp_path: Path,
) -> None:
    """An unmarked or incomplete four-fault package must fail this test."""

    from synthetic.generator.last_lightkeeper import build_universe

    universe = build_universe(tmp_path / "universe")
    ledger = json.loads(universe.hero.fault_ledger.read_text(encoding="utf-8"))

    assert ledger["fixture_id"] == universe.hero.fixture_id
    assert ledger["source_fixture_id"] == universe.clean.fixture_id
    assert ledger["source_asset_hashes"] == dict(universe.hero.source_asset_hashes)
    assert {entry["fault_id"] for entry in ledger["faults"]} == {
        "wrong_filename",
        "missing_checksum_manifest",
        "caption_representation",
        "loudness_requires_human_authority",
    }
    assert all(entry["intentional"] is True for entry in ledger["faults"])
    assert all(entry["generation_error"] is False for entry in ledger["faults"])
    ground_truth = json.loads(universe.ground_truth.read_text(encoding="utf-8"))
    assert ground_truth["hero_fault_ids"] == [
        "wrong_filename",
        "missing_checksum_manifest",
        "caption_representation",
        "loudness_requires_human_authority",
    ]
    assert ground_truth["profile_consequences"]["northstar_broadcast_master_v1"][
        "human_authority_fault_ids"
    ] == ["loudness_requires_human_authority"]
    assert ground_truth["profile_consequences"]["northstar_digital_preview_v1"][
        "human_authority_fault_ids"
    ] == []


def _values(result):
    return {
        (measurement.asset_id, measurement.measurement_key): measurement.value
        for measurement in result.measurements
    }
