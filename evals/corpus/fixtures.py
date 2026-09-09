"""Deterministic scenario package construction for the M6 corpus.

The corpus reuses the frozen M2 synthetic universe (The Last Lightkeeper) as its
ground-truth media. Single-defect scenarios are built by copying the clean
package's assets and applying exactly one controlled, deterministic fault
(a rename, a caption swap, an omitted or corrupt manifest, or one ffmpeg stream
transform). Canonical predecessor fixtures are never mutated in place: every
scenario package is an isolated copy/derivative under a caller-owned directory.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from aircheck.domain.types import AssetRole
from aircheck.runtime.orchestrator import PackageAssetInput
from synthetic.generator.last_lightkeeper import build_universe


BROADCAST_MASTER_NAME = "THE_LAST_LIGHTKEEPER_NSBM_v1.mov"
CLEAN_CAPTIONS_NAME = "THE_LAST_LIGHTKEEPER_NSBM_v1.vtt"
MANIFEST_NAME = "SHA256SUMS"


@dataclass(frozen=True)
class Sources:
    """Absolute paths to the frozen clean/hero universe assets (read-only)."""

    clean_master: Path
    clean_captions: Path
    clean_manifest: Path
    hero_master: Path
    hero_captions: Path


@dataclass(frozen=True)
class ScenarioPackage:
    """A constructed scenario package: an originals directory plus typed inputs."""

    originals: Path
    inputs: tuple[PackageAssetInput, ...]


def build_sources(root: Path) -> Sources:
    """Build the frozen synthetic universe once and expose its asset paths."""

    universe = build_universe(root / "universe")
    assert universe.clean.manifest is not None
    return Sources(
        clean_master=universe.clean.master,
        clean_captions=universe.clean.captions,
        clean_manifest=universe.clean.manifest,
        hero_master=universe.hero.master,
        hero_captions=universe.hero.captions,
    )


def _fresh(root: Path) -> Path:
    originals = root / "originals"
    if originals.exists():
        raise FileExistsError(originals)
    originals.mkdir(parents=True)
    return originals


def _copy(src: Path, dst_dir: Path, name: str) -> Path:
    destination = dst_dir / name
    shutil.copyfile(src, destination)
    return destination


def _write_manifest(dst_dir: Path, entries: tuple[tuple[str, str], ...]) -> Path:
    destination = dst_dir / MANIFEST_NAME
    destination.write_text(
        "".join(f"{digest} *{name}\n" for digest, name in entries),
        encoding="utf-8",
    )
    return destination


def _ffmpeg(args: list[str]) -> None:
    completed = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"corpus fixture ffmpeg failed: {completed.stderr.strip()}")


def clean_broadcast(root: Path, sources: Sources) -> ScenarioPackage:
    """The broadcast-ready clean package (master + captions + manifest)."""

    originals = _fresh(root)
    _copy(sources.clean_master, originals, BROADCAST_MASTER_NAME)
    _copy(sources.clean_captions, originals, CLEAN_CAPTIONS_NAME)
    _copy(sources.clean_manifest, originals, MANIFEST_NAME)
    return ScenarioPackage(
        originals=originals,
        inputs=(
            PackageAssetInput(filename=BROADCAST_MASTER_NAME, role=AssetRole.PROGRAM_MASTER),
            PackageAssetInput(filename=CLEAN_CAPTIONS_NAME, role=AssetRole.CAPTIONS),
            PackageAssetInput(filename=MANIFEST_NAME, role=AssetRole.MANIFEST),
        ),
    )


def hero_all_defects(root: Path, sources: Sources) -> ScenarioPackage:
    """The canonical M2 hero package: four deliberate defects, no manifest."""

    originals = _fresh(root)
    master = _copy(sources.hero_master, originals, sources.hero_master.name)
    captions = _copy(sources.hero_captions, originals, sources.hero_captions.name)
    return ScenarioPackage(
        originals=originals,
        inputs=(
            PackageAssetInput(filename=master.name, role=AssetRole.PROGRAM_MASTER),
            PackageAssetInput(filename=captions.name, role=AssetRole.CAPTIONS),
        ),
    )


def _master_and_clean_captions(
    root: Path, sources: Sources, master_src: Path, master_name: str
) -> ScenarioPackage:
    originals = _fresh(root)
    _copy(master_src, originals, master_name)
    _copy(sources.clean_captions, originals, CLEAN_CAPTIONS_NAME)
    return ScenarioPackage(
        originals=originals,
        inputs=(
            PackageAssetInput(filename=master_name, role=AssetRole.PROGRAM_MASTER),
            PackageAssetInput(filename=CLEAN_CAPTIONS_NAME, role=AssetRole.CAPTIONS),
        ),
    )


def caption_format_defect(root: Path, sources: Sources) -> ScenarioPackage:
    """Clean master + an SRT caption where WebVTT is required; no manifest."""

    originals = _fresh(root)
    _copy(sources.clean_master, originals, BROADCAST_MASTER_NAME)
    captions = _copy(sources.hero_captions, originals, sources.hero_captions.name)
    return ScenarioPackage(
        originals=originals,
        inputs=(
            PackageAssetInput(filename=BROADCAST_MASTER_NAME, role=AssetRole.PROGRAM_MASTER),
            PackageAssetInput(filename=captions.name, role=AssetRole.CAPTIONS),
        ),
    )


def missing_manifest_defect(root: Path, sources: Sources) -> ScenarioPackage:
    """Clean master + clean captions with the checksum manifest omitted."""

    return _master_and_clean_captions(
        root, sources, sources.clean_master, BROADCAST_MASTER_NAME
    )


def loudness_defect(root: Path, sources: Sources) -> ScenarioPackage:
    """The hero master (out-of-window loudness) + clean captions; no manifest."""

    return _master_and_clean_captions(
        root, sources, sources.hero_master, sources.hero_master.name
    )


def missing_captions(root: Path, sources: Sources) -> ScenarioPackage:
    """A package missing the required captions component entirely."""

    originals = _fresh(root)
    _copy(sources.clean_master, originals, BROADCAST_MASTER_NAME)
    return ScenarioPackage(
        originals=originals,
        inputs=(
            PackageAssetInput(filename=BROADCAST_MASTER_NAME, role=AssetRole.PROGRAM_MASTER),
        ),
    )


def codec_defect(root: Path, sources: Sources) -> ScenarioPackage:
    """Clean master re-encoded to a non-compliant video codec (mpeg4)."""

    originals = _fresh(root)
    master = originals / BROADCAST_MASTER_NAME
    _ffmpeg(
        ["-i", str(sources.clean_master), "-c:v", "mpeg4", "-q:v", "5",
         "-c:a", "copy", str(master)]
    )
    _copy(sources.clean_captions, originals, CLEAN_CAPTIONS_NAME)
    return ScenarioPackage(
        originals=originals,
        inputs=(
            PackageAssetInput(filename=BROADCAST_MASTER_NAME, role=AssetRole.PROGRAM_MASTER),
            PackageAssetInput(filename=CLEAN_CAPTIONS_NAME, role=AssetRole.CAPTIONS),
        ),
    )


def frame_rate_defect(root: Path, sources: Sources) -> ScenarioPackage:
    """Clean master re-encoded to a non-compliant frame rate (30 fps)."""

    originals = _fresh(root)
    master = originals / BROADCAST_MASTER_NAME
    # A non-integer rate (29.97) keeps the RATIONAL measurement canonical: an
    # integer rate such as 30 reduces to "30", which the closed catalog rejects.
    _ffmpeg(
        ["-i", str(sources.clean_master), "-c:v", "libx264", "-preset", "ultrafast",
         "-r", "30000/1001", "-c:a", "copy", str(master)]
    )
    _copy(sources.clean_captions, originals, CLEAN_CAPTIONS_NAME)
    return ScenarioPackage(
        originals=originals,
        inputs=(
            PackageAssetInput(filename=BROADCAST_MASTER_NAME, role=AssetRole.PROGRAM_MASTER),
            PackageAssetInput(filename=CLEAN_CAPTIONS_NAME, role=AssetRole.CAPTIONS),
        ),
    )


def channel_count_defect(root: Path, sources: Sources) -> ScenarioPackage:
    """Clean master re-encoded to a non-compliant channel count (mono)."""

    originals = _fresh(root)
    master = originals / BROADCAST_MASTER_NAME
    _ffmpeg(
        ["-i", str(sources.clean_master), "-c:v", "copy", "-ac", "1",
         "-c:a", "pcm_s16le", str(master)]
    )
    _copy(sources.clean_captions, originals, CLEAN_CAPTIONS_NAME)
    return ScenarioPackage(
        originals=originals,
        inputs=(
            PackageAssetInput(filename=BROADCAST_MASTER_NAME, role=AssetRole.PROGRAM_MASTER),
            PackageAssetInput(filename=CLEAN_CAPTIONS_NAME, role=AssetRole.CAPTIONS),
        ),
    )


def caption_timing_defect(root: Path, sources: Sources) -> ScenarioPackage:
    """Clean master + WebVTT captions with overlapping cues; no manifest."""

    originals = _fresh(root)
    _copy(sources.clean_master, originals, BROADCAST_MASTER_NAME)
    captions = originals / CLEAN_CAPTIONS_NAME
    captions.write_text(
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:00.800\nThe last lightkeeper.\n\n"
        "00:00:00.500 --> 00:00:01.000\nAn overlapping cue.\n",
        encoding="utf-8",
    )
    return ScenarioPackage(
        originals=originals,
        inputs=(
            PackageAssetInput(filename=BROADCAST_MASTER_NAME, role=AssetRole.PROGRAM_MASTER),
            PackageAssetInput(filename=CLEAN_CAPTIONS_NAME, role=AssetRole.CAPTIONS),
        ),
    )


def checksum_mismatch(root: Path, sources: Sources) -> ScenarioPackage:
    """Clean master + captions with a foreign manifest listing wrong hashes."""

    originals = _fresh(root)
    _copy(sources.clean_master, originals, BROADCAST_MASTER_NAME)
    _copy(sources.clean_captions, originals, CLEAN_CAPTIONS_NAME)
    _write_manifest(
        originals,
        (
            ("0" * 64, BROADCAST_MASTER_NAME),
            ("1" * 64, CLEAN_CAPTIONS_NAME),
        ),
    )
    return ScenarioPackage(
        originals=originals,
        inputs=(
            PackageAssetInput(filename=BROADCAST_MASTER_NAME, role=AssetRole.PROGRAM_MASTER),
            PackageAssetInput(filename=CLEAN_CAPTIONS_NAME, role=AssetRole.CAPTIONS),
            PackageAssetInput(filename=MANIFEST_NAME, role=AssetRole.MANIFEST),
        ),
    )


def adversarial_package(root: Path, sources: Sources, *, master: str = "clean") -> ScenarioPackage:
    """A clean (or hero-master) package used to run an adversarial specification."""

    master_src = sources.clean_master if master == "clean" else sources.hero_master
    return _master_and_clean_captions(root, sources, master_src, BROADCAST_MASTER_NAME)


__all__ = (
    "Sources",
    "ScenarioPackage",
    "build_sources",
    "clean_broadcast",
    "hero_all_defects",
    "caption_format_defect",
    "missing_manifest_defect",
    "loudness_defect",
    "missing_captions",
    "codec_defect",
    "frame_rate_defect",
    "channel_count_defect",
    "caption_timing_defect",
    "checksum_mismatch",
    "adversarial_package",
)
