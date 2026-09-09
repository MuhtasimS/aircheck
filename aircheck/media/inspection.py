"""M1 deterministic, read-only package and media inspection."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
from typing import Callable

from aircheck.domain.assets import Asset
from aircheck.domain.types import AssetRole, AuthorityTier, MeasurementStatus
from aircheck.tools.contracts import (
    InspectCaptionsInput,
    InspectionResult,
    MeasureLoudnessInput,
    MeasurementValue,
    ProbeMediaInput,
    ScanPackageInput,
    ToolStatus,
    VerifyManifestInput,
    _ToolInput,
    TOOL_REGISTRY,
)
from aircheck.tools.paths import PathIntent, WorkspaceResolver


def hash_file(path: Path) -> str:
    """Return a content hash without changing the inspected asset."""

    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class InspectionContext:
    """Runtime-only ID-to-path context; agent tool inputs never contain paths."""

    run_id: str
    resolver: WorkspaceResolver
    assets: tuple[Asset, ...]

    def __post_init__(self) -> None:
        if not self.assets:
            raise ValueError("inspection context requires at least one asset")
        if any(asset.run_id != self.run_id for asset in self.assets):
            raise ValueError("inspection assets must belong to the context run")
        asset_ids = tuple(asset.asset_id for asset in self.assets)
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("inspection assets must have unique IDs")

    def asset_path(self, asset_id: str) -> tuple[Asset, Path] | None:
        asset = next((item for item in self.assets if item.asset_id == asset_id), None)
        if asset is None:
            return None
        try:
            path = self.resolver.resolve(asset_id, PathIntent.READ)
        except (KeyError, PermissionError):
            return None
        return asset, path


def _ok(*measurements: MeasurementValue) -> InspectionResult:
    return InspectionResult(status=ToolStatus.OK, measurements=measurements)


def _error(code: str, *measurements: MeasurementValue) -> InspectionResult:
    return InspectionResult(
        status=ToolStatus.ERROR,
        measurements=measurements,
        error=code,
    )


def _value(
    measurement_key: str,
    value: str | int | float | bool,
    unit: str | None = None,
    *,
    asset_id: str | None = None,
) -> MeasurementValue:
    return MeasurementValue(
        asset_id=asset_id,
        measurement_key=measurement_key,
        value=value,
        unit=unit,
    )


def _errored_value(
    measurement_key: str,
    unit: str | None,
    code: str,
    *,
    asset_id: str | None,
) -> MeasurementValue:
    return MeasurementValue(
        asset_id=asset_id,
        measurement_key=measurement_key,
        unit=unit,
        status=MeasurementStatus.ERROR,
        error=code,
    )


def scan_package(payload: ScanPackageInput, context: InspectionContext) -> InspectionResult:
    if payload.run_id != context.run_id:
        return _error("RUN_SCOPE_MISMATCH")
    measurements: list[MeasurementValue] = []
    manifest_present = False
    captions_present = False
    for asset in context.assets:
        resolved = context.asset_path(asset.asset_id)
        if resolved is None:
            return _error("ASSET_PATH_UNRESOLVABLE")
        _, path = resolved
        exists = path.is_file()
        measurements.append(
            _value("package.file_present[role]", exists, asset_id=asset.asset_id)
        )
        if exists:
            measurements.append(
                _value("package.filename[role]", path.name, asset_id=asset.asset_id)
            )
        manifest_present = manifest_present or (
            asset.role is AssetRole.MANIFEST and exists
        )
        captions_present = captions_present or (
            asset.role is AssetRole.CAPTIONS and exists
        )
    measurements.extend(
        (
            _value("package.manifest_present", manifest_present),
            _value("captions.present", captions_present),
        )
    )
    return _ok(*measurements)


def _first_stream(payload: dict[str, object], stream_type: str) -> dict[str, object] | None:
    streams = payload.get("streams")
    if not isinstance(streams, list):
        return None
    return next(
        (
            item
            for item in streams
            if isinstance(item, dict) and item.get("codec_type") == stream_type
        ),
        None,
    )


def _canonical_rate(raw: object) -> str | None:
    if not isinstance(raw, str) or raw in {"0/0", "N/A"}:
        return None
    try:
        value = Fraction(raw)
    except (ValueError, ZeroDivisionError):
        return None
    if value <= 0:
        return None
    return f"{value.numerator}/{value.denominator}"


def probe_media(payload: ProbeMediaInput, context: InspectionContext) -> InspectionResult:
    resolved = context.asset_path(payload.asset_id)
    if resolved is None:
        return _error("UNKNOWN_ASSET")
    asset, path = resolved
    if not path.is_file():
        return _error("ASSET_MISSING")
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return _error("PROBE_FAILED")
    try:
        payload_json = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return _error("PROBE_OUTPUT_INVALID")
    if not isinstance(payload_json, dict):
        return _error("PROBE_OUTPUT_INVALID")

    measurements: list[MeasurementValue] = []
    format_info = payload_json.get("format")
    if isinstance(format_info, dict):
        format_names = format_info.get("format_name")
        if isinstance(format_names, str) and format_names:
            names = tuple(name.casefold() for name in format_names.split(","))
            container = "quicktime mov" if "mov" in names else names[0]
            measurements.append(_value("container.format", container, asset_id=asset.asset_id))

    video = _first_stream(payload_json, "video")
    if video is not None:
        codec = video.get("codec_name")
        if isinstance(codec, str) and codec:
            measurements.append(_value("video.codec", codec.casefold(), asset_id=asset.asset_id))
        for key, measurement_key in (("width", "video.width"), ("height", "video.height")):
            value = video.get(key)
            if isinstance(value, int) and value > 0:
                measurements.append(_value(measurement_key, value, "px", asset_id=asset.asset_id))
        frame_rate = _canonical_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
        if frame_rate is not None:
            measurements.append(_value("video.frame_rate", frame_rate, "fps", asset_id=asset.asset_id))
        scan_type = video.get("field_order")
        canonical_scan_type = (
            scan_type.casefold()
            if isinstance(scan_type, str) and scan_type
            else "unknown"
        )
        measurements.append(
            _value("video.scan_type", canonical_scan_type, asset_id=asset.asset_id)
        )

    audio = _first_stream(payload_json, "audio")
    if audio is not None:
        channels = audio.get("channels")
        if isinstance(channels, int) and channels >= 0:
            measurements.append(_value("audio.channel_count", channels, asset_id=asset.asset_id))
        layout = audio.get("channel_layout")
        if isinstance(layout, str) and layout:
            measurements.append(_value("audio.channel_layout", layout.casefold(), asset_id=asset.asset_id))
        sample_rate = audio.get("sample_rate")
        try:
            parsed_rate = int(sample_rate)  # ffprobe writes this numeric field as text.
        except (TypeError, ValueError):
            parsed_rate = 0
        if parsed_rate > 0:
            measurements.append(_value("audio.sample_rate", parsed_rate, "Hz", asset_id=asset.asset_id))
    return _ok(*measurements)


def _loudness_json(output: str) -> dict[str, object] | None:
    matches = re.findall(r"\{\s*\"input_i\".*?\}", output, flags=re.DOTALL)
    if not matches:
        return None
    try:
        value = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def measure_loudness(
    payload: MeasureLoudnessInput,
    context: InspectionContext,
) -> InspectionResult:
    if payload.stream != "primary_audio":
        return _error("UNSUPPORTED_AUDIO_STREAM")
    resolved = context.asset_path(payload.asset_id)
    if resolved is None:
        return _error("UNKNOWN_ASSET")
    asset, path = resolved
    if not path.is_file():
        return _error("ASSET_MISSING")
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-v",
            "info",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "loudnorm=I=-24:TP=-2:LRA=7:print_format=json",
            "-f",
            "null",
            "-",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return _error("NO_AUDIO_STREAM")
    report = _loudness_json(completed.stderr + completed.stdout)
    if report is None:
        return _error("LOUDNESS_OUTPUT_INVALID")
    try:
        integrated = float(report["input_i"])
        true_peak = float(report["input_tp"])
        loudness_range = float(report["input_lra"])
    except (KeyError, TypeError, ValueError):
        return _error("LOUDNESS_OUTPUT_INVALID")
    return _ok(
        _value("audio.integrated_loudness", integrated, "LUFS", asset_id=asset.asset_id),
        _value("audio.true_peak", true_peak, "dBTP", asset_id=asset.asset_id),
        _value("audio.loudness_range", loudness_range, "LU", asset_id=asset.asset_id),
    )


_TIMING = re.compile(
    r"^(?P<start>[0-9]{2}:[0-9]{2}:[0-9]{2}[,.][0-9]{3})\s+-->\s+"
    r"(?P<end>[0-9]{2}:[0-9]{2}:[0-9]{2}[,.][0-9]{3})(?:\s+.*)?$"
)


def _timestamp(value: str) -> float:
    hours, minutes, seconds, milliseconds = re.split(r"[:,.]", value)
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(milliseconds) / 1000
    )


def _caption_failure(asset_id: str, code: str) -> InspectionResult:
    timing_keys = (
        ("captions.cue_count", None),
        ("captions.first_cue_start", "s"),
        ("captions.overlapping_cues", None),
        ("captions.max_cue_end_vs_duration", "s"),
    )
    return _error(
        code,
        _value("captions.format", "unknown", asset_id=asset_id),
        *(
            _errored_value(key, unit, code, asset_id=asset_id)
            for key, unit in timing_keys
        ),
    )


def inspect_captions(
    payload: InspectCaptionsInput,
    context: InspectionContext,
) -> InspectionResult:
    resolved = context.asset_path(payload.asset_id)
    if resolved is None:
        return _error("UNKNOWN_ASSET")
    asset, path = resolved
    if not path.is_file():
        return _error("ASSET_MISSING")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _caption_failure(asset.asset_id, "CAPTION_FILE_UNREADABLE")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if normalized.startswith("WEBVTT"):
        caption_format = "webvtt"
        body = normalized[len("WEBVTT") :].lstrip("\n")
    elif path.suffix.casefold() == ".srt":
        caption_format = "srt"
        body = normalized
    else:
        return _caption_failure(asset.asset_id, "CAPTION_FORMAT_UNKNOWN")
    cues: list[tuple[float, float]] = []
    for block in re.split(r"\n\s*\n", body):
        lines = tuple(line.strip() for line in block.splitlines() if line.strip())
        timing_line = next((line for line in lines if "-->" in line), None)
        if timing_line is None:
            return _caption_failure(asset.asset_id, "CAPTION_TIMING_UNPARSEABLE")
        match = _TIMING.fullmatch(timing_line)
        if match is None:
            return _caption_failure(asset.asset_id, "CAPTION_TIMING_UNPARSEABLE")
        start = _timestamp(match.group("start"))
        end = _timestamp(match.group("end"))
        if end < start:
            return _caption_failure(asset.asset_id, "CAPTION_TIMING_UNPARSEABLE")
        cues.append((start, end))
    if not cues:
        return _caption_failure(asset.asset_id, "CAPTION_TIMING_UNPARSEABLE")
    ordered = sorted(cues)
    overlaps = sum(1 for (_, end), (start, _) in zip(ordered, ordered[1:]) if end > start)
    measurements: list[MeasurementValue] = [
        _value("captions.format", caption_format, asset_id=asset.asset_id),
        _value("captions.cue_count", len(cues), asset_id=asset.asset_id),
        _value("captions.first_cue_start", ordered[0][0], "s", asset_id=asset.asset_id),
        _value("captions.overlapping_cues", overlaps, asset_id=asset.asset_id),
    ]
    if payload.program_duration_s is not None:
        measurements.append(
            _value(
                "captions.max_cue_end_vs_duration",
                round(max(end for _, end in cues) - payload.program_duration_s, 6),
                "s",
                asset_id=asset.asset_id,
            )
        )
    return _ok(*measurements)


def _safe_manifest_name(value: str) -> bool:
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    return not (
        windows.is_absolute()
        or posix.is_absolute()
        or ".." in windows.parts
        or ".." in posix.parts
        or "/" in value
        or "\\" in value
    )


def verify_manifest(
    payload: VerifyManifestInput,
    context: InspectionContext,
) -> InspectionResult:
    if payload.run_id != context.run_id:
        return _error("RUN_SCOPE_MISMATCH")
    manifests = tuple(asset for asset in context.assets if asset.role is AssetRole.MANIFEST)
    if len(manifests) != 1:
        return _error("MANIFEST_ABSENT")
    manifest_resolved = context.asset_path(manifests[0].asset_id)
    if manifest_resolved is None or not manifest_resolved[1].is_file():
        return _error("MANIFEST_ABSENT")
    try:
        lines = manifest_resolved[1].read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return _error("MANIFEST_UNREADABLE")
    entries: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9A-Fa-f]{64})\s+\*?([^\s]+)", line)
        if match is None or not _safe_manifest_name(match.group(2)):
            return _error("MANIFEST_MALFORMED")
        expected_hash, filename = match.groups()
        if filename in entries:
            return _error("MANIFEST_MALFORMED")
        entries[filename] = expected_hash.casefold()
    package_assets = tuple(asset for asset in context.assets if asset.role is not AssetRole.MANIFEST)
    expected_names = {asset.filename for asset in package_assets}
    checksums_match = set(entries) == expected_names
    for asset in package_assets:
        resolved = context.asset_path(asset.asset_id)
        if resolved is None or not resolved[1].is_file():
            checksums_match = False
            continue
        checksums_match = checksums_match and entries.get(asset.filename) == hash_file(resolved[1])
    return _ok(_value("package.checksums_match", checksums_match))


_HANDLERS: dict[str, Callable[[_ToolInput, InspectionContext], InspectionResult]] = {
    "scan_package": scan_package,
    "probe_media": probe_media,
    "measure_loudness": measure_loudness,
    "inspect_captions": inspect_captions,
    "verify_manifest": verify_manifest,
}


def execute_inspection(
    name: str,
    payload: _ToolInput,
    context: InspectionContext,
) -> InspectionResult:
    """Execute an implemented Tier-0 tool under runtime-owned path resolution."""

    specification = TOOL_REGISTRY.get(name)
    if specification is None or specification.authority_tier is not AuthorityTier.INSPECT:
        raise TypeError("execute_inspection accepts only implemented Tier-0 tools")
    if not isinstance(payload, specification.input_model):
        raise TypeError(f"{name} requires {specification.input_model.__name__}")
    return _HANDLERS[name](payload, context)
