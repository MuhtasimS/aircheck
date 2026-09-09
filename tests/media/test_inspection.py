"""M1 read-only deterministic media/package inspection tests."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil
import subprocess

import pytest

from aircheck.domain.assets import Asset
from aircheck.domain.types import AssetProtection, AssetRole
from aircheck.tools.contracts import (
    InspectCaptionsInput,
    MeasureLoudnessInput,
    ProbeMediaInput,
    ScanPackageInput,
    ToolStatus,
    VerifyManifestInput,
)
from aircheck.tools.paths import PathIntent, RuntimeAssetPath, WorkspaceResolver


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _asset(asset_id: str, role: AssetRole, path: Path) -> Asset:
    return Asset(
        asset_id=asset_id,
        run_id="run_m1_truth_001",
        role=role,
        filename=path.name,
        sha256=_sha256(path),
        size_bytes=path.stat().st_size,
        protection=AssetProtection.ORIGINAL,
    )


@pytest.fixture
def inspection_context(tmp_path: Path):
    assert shutil.which("ffmpeg"), "M1 requires ffmpeg"
    assert shutil.which("ffprobe"), "M1 requires ffprobe"

    originals = tmp_path / "originals"
    originals.mkdir()
    master = originals / "master.mov"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24000/1001",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000",
            "-filter:a",
            "volume=-6dB",
            "-t",
            "1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "mpeg4",
            "-c:a",
            "pcm_s16le",
            "-ac",
            "2",
            str(master),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    captions = originals / "captions.vtt"
    captions.write_text(
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:00.500\nHello\n\n"
        "00:00:00.600 --> 00:00:01.000\nWorld\n",
        encoding="utf-8",
    )
    manifest = originals / "checksums.sha256"
    manifest.write_text(
        f"{_sha256(master)} *{master.name}\n{_sha256(captions)} *{captions.name}\n",
        encoding="utf-8",
    )

    assets = (
        _asset("asset_m1_master", AssetRole.PROGRAM_MASTER, master),
        _asset("asset_m1_captions", AssetRole.CAPTIONS, captions),
        _asset("asset_m1_manifest", AssetRole.MANIFEST, manifest),
    )
    resolver = WorkspaceResolver(
        tmp_path,
        tuple(
            RuntimeAssetPath(
                asset=asset,
                relative_path=f"originals/{asset.filename}",
            )
            for asset in assets
        ),
    )
    from aircheck.media.inspection import InspectionContext

    return InspectionContext(run_id="run_m1_truth_001", resolver=resolver, assets=assets)


def _values(result):
    return {(value.asset_id, value.measurement_key): value.value for value in result.measurements}


def _context(root: Path, assets: tuple[Asset, ...]):
    from aircheck.media.inspection import InspectionContext

    return InspectionContext(
        run_id="run_m1_truth_001",
        resolver=WorkspaceResolver(
            root,
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


def test_caption_timestamp_parses_hours_minutes_seconds_and_milliseconds() -> None:
    from aircheck.media.inspection import _timestamp

    assert _timestamp("01:02:03.456") == pytest.approx(3723.456)


def test_tier_zero_tools_prove_known_fixture_facts_and_preserve_originals(
    inspection_context,
) -> None:
    from aircheck.media.inspection import execute_inspection

    master_path = inspection_context.resolver.resolve("asset_m1_master", PathIntent.READ)
    before = {
        asset.asset_id: _sha256(
            inspection_context.resolver.resolve(asset.asset_id, PathIntent.READ)
        )
        for asset in inspection_context.assets
    }
    scan = execute_inspection(
        "scan_package", ScanPackageInput(run_id="run_m1_truth_001"), inspection_context
    )
    probe = execute_inspection(
        "probe_media", ProbeMediaInput(asset_id="asset_m1_master"), inspection_context
    )
    loudness = execute_inspection(
        "measure_loudness",
        MeasureLoudnessInput(asset_id="asset_m1_master", stream="primary_audio"),
        inspection_context,
    )
    captions = execute_inspection(
        "inspect_captions",
        InspectCaptionsInput(asset_id="asset_m1_captions", program_duration_s=1.0),
        inspection_context,
    )
    manifest = execute_inspection(
        "verify_manifest", VerifyManifestInput(run_id="run_m1_truth_001"), inspection_context
    )

    assert all(result.status is ToolStatus.OK for result in (scan, probe, loudness, captions, manifest))
    assert _values(scan)[("asset_m1_master", "package.file_present[role]")] is True
    assert _values(scan)[("asset_m1_master", "package.filename[role]")] == "master.mov"
    assert _values(scan)[(None, "package.manifest_present")] is True
    assert _values(scan)[(None, "captions.present")] is True
    assert _values(probe) == {
        ("asset_m1_master", "container.format"): "quicktime mov",
        ("asset_m1_master", "video.codec"): "mpeg4",
        ("asset_m1_master", "video.width"): 320,
        ("asset_m1_master", "video.height"): 180,
        ("asset_m1_master", "video.frame_rate"): "24000/1001",
        ("asset_m1_master", "video.scan_type"): "unknown",
        ("asset_m1_master", "audio.channel_count"): 2,
        ("asset_m1_master", "audio.channel_layout"): "stereo",
        ("asset_m1_master", "audio.sample_rate"): 48000,
    }
    loudness_values = _values(loudness)
    assert loudness_values[("asset_m1_master", "audio.integrated_loudness")] == pytest.approx(-27.0, abs=0.1)
    assert loudness_values[("asset_m1_master", "audio.true_peak")] == pytest.approx(-27.08, abs=0.1)
    assert loudness_values[("asset_m1_master", "audio.loudness_range")] == pytest.approx(0.0, abs=0.1)
    assert _values(captions) == {
        ("asset_m1_captions", "captions.format"): "webvtt",
        ("asset_m1_captions", "captions.cue_count"): 2,
        ("asset_m1_captions", "captions.first_cue_start"): 0.0,
        ("asset_m1_captions", "captions.overlapping_cues"): 0,
        ("asset_m1_captions", "captions.max_cue_end_vs_duration"): 0.0,
    }
    assert _values(manifest) == {(None, "package.checksums_match"): True}
    assert {
        asset.asset_id: _sha256(
            inspection_context.resolver.resolve(asset.asset_id, PathIntent.READ)
        )
        for asset in inspection_context.assets
    } == before


def test_each_tier_zero_inspection_is_idempotent(inspection_context) -> None:
    from aircheck.media.inspection import execute_inspection

    operations = (
        ("scan_package", ScanPackageInput(run_id="run_m1_truth_001")),
        ("probe_media", ProbeMediaInput(asset_id="asset_m1_master")),
        (
            "measure_loudness",
            MeasureLoudnessInput(asset_id="asset_m1_master", stream="primary_audio"),
        ),
        (
            "inspect_captions",
            InspectCaptionsInput(asset_id="asset_m1_captions", program_duration_s=1.0),
        ),
        ("verify_manifest", VerifyManifestInput(run_id="run_m1_truth_001")),
    )
    for name, payload in operations:
        assert execute_inspection(name, payload, inspection_context) == execute_inspection(
            name, payload, inspection_context
        )


def test_malformed_caption_and_missing_manifest_are_typed_non_pass_failures(
    inspection_context,
) -> None:
    from aircheck.media.inspection import InspectionContext, execute_inspection

    root = inspection_context.resolver.resolve(
        "asset_m1_master", PathIntent.READ
    ).parents[1]
    bad_captions = root / "originals" / "malformed.vtt"
    bad_captions.write_text("WEBVTT\n\nthis is not a cue\n", encoding="utf-8")
    bad_asset = _asset("asset_m1_bad_captions", AssetRole.CAPTIONS, bad_captions)
    assets = (*inspection_context.assets, bad_asset)
    resolver = WorkspaceResolver(
        root,
        tuple(
            RuntimeAssetPath(
                asset=asset,
                relative_path=f"originals/{asset.filename}",
            )
            for asset in assets
        ),
    )
    malformed_context = InspectionContext(
        run_id="run_m1_truth_001", resolver=resolver, assets=assets
    )
    malformed = execute_inspection(
        "inspect_captions",
        InspectCaptionsInput(asset_id="asset_m1_bad_captions", program_duration_s=1.0),
        malformed_context,
    )
    no_manifest_assets = tuple(
        asset for asset in inspection_context.assets if asset.role is not AssetRole.MANIFEST
    )
    no_manifest_context = InspectionContext(
        run_id="run_m1_truth_001",
        resolver=inspection_context.resolver,
        assets=no_manifest_assets,
    )
    missing_manifest = execute_inspection(
        "verify_manifest", VerifyManifestInput(run_id="run_m1_truth_001"), no_manifest_context
    )

    assert malformed.status is ToolStatus.ERROR
    assert malformed.error == "CAPTION_TIMING_UNPARSEABLE"
    assert _values(malformed)[("asset_m1_bad_captions", "captions.format")] == "unknown"
    assert all(
        value.status.value == "ERROR"
        for value in malformed.measurements
        if value.measurement_key != "captions.format"
    )
    assert missing_manifest.status is ToolStatus.ERROR
    assert missing_manifest.error == "MANIFEST_ABSENT"


def test_srt_and_manifest_mismatch_have_deterministic_truthful_results(
    inspection_context,
) -> None:
    from aircheck.media.inspection import execute_inspection

    root = inspection_context.resolver.resolve(
        "asset_m1_master", PathIntent.READ
    ).parents[1]
    srt = root / "originals" / "captions.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:00,400\nOne\n\n"
        "2\n00:00:00,400 --> 00:00:00,800\nTwo\n",
        encoding="utf-8",
    )
    mismatch = root / "originals" / "mismatch.sha256"
    mismatch.write_text("0" * 64 + " *master.mov\n", encoding="utf-8")
    srt_asset = _asset("asset_m1_srt", AssetRole.CAPTIONS, srt)
    mismatch_asset = _asset("asset_m1_bad_manifest", AssetRole.MANIFEST, mismatch)
    assets = tuple(
        asset
        for asset in inspection_context.assets
        if asset.role is not AssetRole.MANIFEST
    ) + (srt_asset, mismatch_asset)
    context = _context(root, assets)

    srt_result = execute_inspection(
        "inspect_captions",
        InspectCaptionsInput(asset_id="asset_m1_srt", program_duration_s=1.0),
        context,
    )
    mismatch_result = execute_inspection(
        "verify_manifest", VerifyManifestInput(run_id="run_m1_truth_001"), context
    )

    assert srt_result.status is ToolStatus.OK
    assert _values(srt_result)[("asset_m1_srt", "captions.format")] == "srt"
    assert _values(srt_result)[("asset_m1_srt", "captions.overlapping_cues")] == 0
    assert mismatch_result.status is ToolStatus.OK
    assert _values(mismatch_result) == {(None, "package.checksums_match"): False}


def test_loudness_rejects_no_audio_and_unknown_stream(inspection_context) -> None:
    from aircheck.media.inspection import execute_inspection

    no_audio = execute_inspection(
        "measure_loudness",
        MeasureLoudnessInput(asset_id="asset_m1_captions", stream="primary_audio"),
        inspection_context,
    )
    wrong_stream = execute_inspection(
        "measure_loudness",
        MeasureLoudnessInput(asset_id="asset_m1_master", stream="secondary_audio"),
        inspection_context,
    )

    assert no_audio.status is ToolStatus.ERROR
    assert no_audio.error == "NO_AUDIO_STREAM"
    assert wrong_stream.status is ToolStatus.ERROR
    assert wrong_stream.error == "UNSUPPORTED_AUDIO_STREAM"


def test_inspection_refuses_unknown_assets_and_non_tier_zero_tools(inspection_context) -> None:
    from aircheck.media.inspection import execute_inspection
    from aircheck.tools.contracts import RenameDeliveryCopyInput

    unknown = execute_inspection(
        "probe_media", ProbeMediaInput(asset_id="asset_unknown"), inspection_context
    )
    assert unknown.status is ToolStatus.ERROR
    assert unknown.error == "UNKNOWN_ASSET"
    with pytest.raises(TypeError, match="Tier-0"):
        execute_inspection(
            "rename_delivery_copy",
            RenameDeliveryCopyInput(
                asset_id="asset_m1_master",
                option_id="option_m1_rename",
                new_filename="master_renamed.mov",
            ),
            inspection_context,
        )
