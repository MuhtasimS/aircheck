"""Validation tests for the two fictional Northstar destination profiles."""

import json
import hashlib
from pathlib import Path

import pytest

SPEC_ROOT = Path(__file__).parents[2] / "specifications" / "northstar"
REPO_ROOT = Path(__file__).parents[2]
LEGACY_HASHES = {
    "broadcast_master.json": "652c45a32929ed6a6865dd589853d17fd2c744b72716fc6ee95c18c0771dda5a",
    "digital_preview.json": "c428106bbb788d9ba70c4b6c9fefdb6a0f30884e9d8f1dabed434c3748e50408",
}


def load_profile(name: str) -> dict[str, object]:
    with (SPEC_ROOT / name).open(encoding="utf-8") as profile_file:
        return json.load(profile_file)


@pytest.mark.parametrize("filename", tuple(LEGACY_HASHES))
def test_legacy_m0_northstar_fixture_hash_is_frozen(filename: str) -> None:
    """R2 must preserve M0 inputs without pretending they are final executable profiles."""
    raw = (SPEC_ROOT / filename).read_bytes()
    profile = load_profile(filename)

    assert hashlib.sha256(raw).hexdigest() == LEGACY_HASHES[filename]
    assert profile["requirements"]
    assert all(
        item["source_reference"].startswith("Northstar ")
        for item in profile["requirements"]
    )


def test_profiles_have_distinct_destination_names() -> None:
    """Collapsing both fixtures into one profile would erase adaptive planning."""
    broadcast = load_profile("broadcast_master.json")
    preview = load_profile("digital_preview.json")

    assert broadcast["profile_name"] == "Northstar Broadcast Master"
    assert preview["profile_name"] == "Northstar Digital Preview"


def test_same_source_yields_materially_different_qc_constraints() -> None:
    """Five domain-level differences prove the profiles are not label variants."""
    broadcast = load_profile("broadcast_master.json")
    preview = load_profile("digital_preview.json")
    broadcast_by_category = {
        item["category"]: item["normalized_constraint"]
        for item in broadcast["requirements"]
    }
    preview_by_category = {
        item["category"]: item["normalized_constraint"]
        for item in preview["requirements"]
    }

    differing_categories = {
        category
        for category in {"VIDEO", "AUDIO", "CAPTIONS", "NAMING", "PACKAGE"}
        if broadcast_by_category[category] != preview_by_category[category]
    }
    assert differing_categories == {"VIDEO", "AUDIO", "CAPTIONS", "NAMING", "PACKAGE"}


def test_r2_handoff_directories_preserve_legacy_fixtures_through_m2() -> None:
    required = (
        SPEC_ROOT / "source" / "README.md",
        SPEC_ROOT / "profiles" / "README.md",
        SPEC_ROOT / "legacy_m0" / "README.md",
        REPO_ROOT / "evals" / "specs" / "README.md",
        REPO_ROOT / "evals" / "corpus" / "README.md",
    )
    assert all(path.is_file() for path in required)
    assert (SPEC_ROOT / "profiles" / "m2_product_profiles.json").is_file()
    assert (SPEC_ROOT / "source" / "broadcast_master.md").is_file()
    assert (SPEC_ROOT / "source" / "digital_preview.md").is_file()
    assert (REPO_ROOT / "evals" / "expected" / "ambiguous.json").is_file()
