"""Deterministically build The Last Lightkeeper M2 fixture universe."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
from types import MappingProxyType
from typing import Mapping

from aircheck.domain.assets import Asset
from aircheck.domain.types import AssetProtection, AssetRole
from aircheck.media.inspection import InspectionContext, execute_inspection
from aircheck.tools.contracts import (
    InspectCaptionsInput,
    InspectionResult,
    MeasureLoudnessInput,
    ProbeMediaInput,
    ScanPackageInput,
    VerifyManifestInput,
)
from aircheck.tools.paths import RuntimeAssetPath, WorkspaceResolver
from synthetic.fault_injection.hero import inject_hero_faults


PROGRAM_TITLE = "The Last Lightkeeper"
CLEAN_FIXTURE_ID = "the_last_lightkeeper_clean_broadcast_v1"
HERO_FIXTURE_ID = "the_last_lightkeeper_hero_faults_v1"
CLEAN_MASTER_FILENAME = "THE_LAST_LIGHTKEEPER_NSBM_v1.mov"
CLEAN_CAPTIONS_FILENAME = "THE_LAST_LIGHTKEEPER_NSBM_v1.vtt"
MANIFEST_FILENAME = "SHA256SUMS"
_RUN_IDS = {CLEAN_FIXTURE_ID: "run_m2_clean_001", HERO_FIXTURE_ID: "run_m2_hero_001"}


@dataclass(frozen=True)
class SyntheticPackage:
    """One generated package and its fixture-only provenance."""

    fixture_id: str
    root: Path
    originals: Path
    master: Path
    captions: Path
    manifest: Path | None
    source_fixture_id: str | None
    source_asset_hashes: Mapping[str, str]
    fault_ledger: Path | None


@dataclass(frozen=True)
class SyntheticUniverse:
    """The clean and deliberately faulted packages for the M2 story."""

    root: Path
    clean: SyntheticPackage
    hero: SyntheticPackage
    ground_truth: Path


def snapshot_hashes(root: Path) -> dict[str, str]:
    """Return stable relative SHA-256 facts for a fixture tree."""

    return {
        path.relative_to(root).as_posix(): _hash_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def build_universe(destination: Path) -> SyntheticUniverse:
    """Build both M2 packages in an empty caller-owned destination."""

    if destination.exists():
        raise FileExistsError(destination)
    _require_media_tools()
    destination.mkdir(parents=True)
    clean = _build_clean_package(destination / "clean_broadcast")
    clean_original_hashes = snapshot_hashes(clean.originals)
    hero = _build_hero_package(destination / "hero_faults", clean, clean_original_hashes)
    ground_truth = destination / "m2_ground_truth.json"
    _write_json(
        ground_truth,
        {
            "schema_version": "m2_synthetic_ground_truth_v1",
            "program_title": PROGRAM_TITLE,
            "clean_fixture_id": clean.fixture_id,
            "hero_fixture_id": hero.fixture_id,
            "hero_fault_ids": [
                "wrong_filename",
                "missing_checksum_manifest",
                "caption_representation",
                "loudness_requires_human_authority",
            ],
            "profile_consequences": {
                "northstar_broadcast_master_v1": {
                    "safe_fault_ids": [
                        "wrong_filename",
                        "missing_checksum_manifest",
                        "caption_representation",
                    ],
                    "human_authority_fault_ids": [
                        "loudness_requires_human_authority"
                    ],
                },
                "northstar_digital_preview_v1": {
                    "safe_fault_ids": ["wrong_filename"],
                    "human_authority_fault_ids": [],
                },
            },
        },
    )
    return SyntheticUniverse(
        root=destination,
        clean=clean,
        hero=hero,
        ground_truth=ground_truth,
    )


def inspect_package(package: SyntheticPackage) -> dict[str, InspectionResult]:
    """Run the implemented M1 Tier-0 tools against one generated package."""

    run_id = _RUN_IDS[package.fixture_id]
    assets = _inspection_assets(package, run_id)
    context = InspectionContext(
        run_id=run_id,
        resolver=WorkspaceResolver(
            package.root,
            tuple(
                RuntimeAssetPath(
                    asset=asset,
                    relative_path=f"originals/{asset.filename}",
                )
                for asset in assets
            ),
        ),
        assets=assets,
    )
    master_id = f"asset_{'clean' if package.fixture_id == CLEAN_FIXTURE_ID else 'hero'}_master"
    captions_id = f"asset_{'clean' if package.fixture_id == CLEAN_FIXTURE_ID else 'hero'}_captions"
    return {
        "scan_package": execute_inspection(
            "scan_package", ScanPackageInput(run_id=run_id), context
        ),
        "probe_media": execute_inspection(
            "probe_media", ProbeMediaInput(asset_id=master_id), context
        ),
        "measure_loudness": execute_inspection(
            "measure_loudness",
            MeasureLoudnessInput(asset_id=master_id, stream="primary_audio"),
            context,
        ),
        "inspect_captions": execute_inspection(
            "inspect_captions",
            InspectCaptionsInput(asset_id=captions_id, program_duration_s=1.0),
            context,
        ),
        "verify_manifest": execute_inspection(
            "verify_manifest", VerifyManifestInput(run_id=run_id), context
        ),
    }


def _build_clean_package(root: Path) -> SyntheticPackage:
    originals = root / "originals"
    originals.mkdir(parents=True)
    master = originals / CLEAN_MASTER_FILENAME
    captions = originals / CLEAN_CAPTIONS_FILENAME
    manifest = originals / MANIFEST_FILENAME
    _generate_program_media(master, volume_db=-3)
    captions.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:00.500\nThe last lightkeeper.\n\n"
        "00:00:00.500 --> 00:00:01.000\nA Northstar master.\n",
        encoding="utf-8",
    )
    _write_manifest(manifest, (master, captions))
    source_hashes = MappingProxyType(snapshot_hashes(originals))
    _write_json(
        root / "fixture.json",
        {
            "fixture_id": CLEAN_FIXTURE_ID,
            "program_title": PROGRAM_TITLE,
            "kind": "clean_broadcast_package",
            "asset_hashes": dict(source_hashes),
            "expected_m1": {
                "captions.format": "webvtt",
                "package.manifest_present": True,
                "package.checksums_match": True,
                "audio.integrated_loudness_lufs": -24.0,
            },
        },
    )
    return SyntheticPackage(
        fixture_id=CLEAN_FIXTURE_ID,
        root=root,
        originals=originals,
        master=master,
        captions=captions,
        manifest=manifest,
        source_fixture_id=None,
        source_asset_hashes=source_hashes,
        fault_ledger=None,
    )


def _build_hero_package(
    root: Path,
    clean: SyntheticPackage,
    clean_original_hashes: Mapping[str, str],
) -> SyntheticPackage:
    originals = root / "originals"
    files = inject_hero_faults(clean_master=clean.master, destination=originals)
    source_hashes = MappingProxyType(dict(clean_original_hashes))
    ledger = root / "faults.json"
    _write_json(
        ledger,
        {
            "fixture_id": HERO_FIXTURE_ID,
            "source_fixture_id": CLEAN_FIXTURE_ID,
            "source_asset_hashes": dict(source_hashes),
            "faults": [
                {
                    "fault_id": "wrong_filename",
                    "intentional": True,
                    "generation_error": False,
                    "observed_filename": files.master.name,
                    "broadcast_expected_filename": CLEAN_MASTER_FILENAME,
                    "preview_expected_filename": "the-last-lightkeeper-preview-v1.mov",
                },
                {
                    "fault_id": "missing_checksum_manifest",
                    "intentional": True,
                    "generation_error": False,
                    "omitted_filename": MANIFEST_FILENAME,
                },
                {
                    "fault_id": "caption_representation",
                    "intentional": True,
                    "generation_error": False,
                    "observed_format": "srt",
                    "broadcast_required_format": "webvtt",
                },
                {
                    "fault_id": "loudness_requires_human_authority",
                    "intentional": True,
                    "generation_error": False,
                    "target_lufs": -19.0,
                    "broadcast_range_lufs": [-26.0, -22.0],
                    "preview_range_lufs": [-21.0, -17.0],
                },
            ],
        },
    )
    _write_json(
        root / "fixture.json",
        {
            "fixture_id": HERO_FIXTURE_ID,
            "program_title": PROGRAM_TITLE,
            "kind": "intentional_broadcast_fault_package",
            "source_fixture_id": CLEAN_FIXTURE_ID,
            "source_asset_hashes": dict(source_hashes),
            "asset_hashes": snapshot_hashes(originals),
        },
    )
    return SyntheticPackage(
        fixture_id=HERO_FIXTURE_ID,
        root=root,
        originals=originals,
        master=files.master,
        captions=files.captions,
        manifest=None,
        source_fixture_id=CLEAN_FIXTURE_ID,
        source_asset_hashes=source_hashes,
        fault_ledger=ledger,
    )


def _generate_program_media(destination: Path, *, volume_db: int) -> None:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=24000/1001:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=1",
            "-filter:a",
            f"volume={volume_db}dB",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-threads",
            "1",
            "-x264-params",
            "scenecut=0:open_gop=0",
            "-c:a",
            "pcm_s16le",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-metadata",
            "creation_time=1970-01-01T00:00:00Z",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"M2 program generation failed: {completed.stderr.strip()}")


def _inspection_assets(package: SyntheticPackage, run_id: str) -> tuple[Asset, ...]:
    prefix = "clean" if package.fixture_id == CLEAN_FIXTURE_ID else "hero"
    values = [
        _asset(
            asset_id=f"asset_{prefix}_master",
            run_id=run_id,
            role=AssetRole.PROGRAM_MASTER,
            path=package.master,
        ),
        _asset(
            asset_id=f"asset_{prefix}_captions",
            run_id=run_id,
            role=AssetRole.CAPTIONS,
            path=package.captions,
        ),
    ]
    if package.manifest is not None:
        values.append(
            _asset(
                asset_id=f"asset_{prefix}_manifest",
                run_id=run_id,
                role=AssetRole.MANIFEST,
                path=package.manifest,
            )
        )
    return tuple(values)


def _asset(*, asset_id: str, run_id: str, role: AssetRole, path: Path) -> Asset:
    return Asset(
        asset_id=asset_id,
        run_id=run_id,
        role=role,
        filename=path.name,
        sha256=_hash_file(path),
        size_bytes=path.stat().st_size,
        protection=AssetProtection.ORIGINAL,
    )


def _write_manifest(destination: Path, assets: tuple[Path, ...]) -> None:
    destination.write_text(
        "".join(f"{_hash_file(asset)} *{asset.name}\n" for asset in assets),
        encoding="utf-8",
    )


def _write_json(destination: Path, value: object) -> None:
    destination.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_media_tools() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("M2 requires ffmpeg and ffprobe")
