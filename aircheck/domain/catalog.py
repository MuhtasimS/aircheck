"""Closed, deterministic measurement vocabulary for executable requirements."""

from __future__ import annotations

from fractions import Fraction
from math import isfinite
import re

from aircheck.domain.primitives import CanonicalScalar, FrozenModel, NonEmptyStr
from aircheck.domain.types import (
    AutomationDisposition,
    ConstraintOperator,
    MeasurementValueType,
)


class MeasurementDefinition(FrozenModel):
    measurement_key: NonEmptyStr
    label: NonEmptyStr
    value_type: MeasurementValueType
    canonical_unit: NonEmptyStr | None
    produced_by: NonEmptyStr
    remediation_default: AutomationDisposition
    remediation_tool: NonEmptyStr | None = None


def _entry(
    key: str,
    label: str,
    value_type: MeasurementValueType,
    unit: str | None,
    tool: str,
    disposition: AutomationDisposition = AutomationDisposition.REPORT_ONLY,
    remediation_tool: str | None = None,
) -> MeasurementDefinition:
    return MeasurementDefinition(
        measurement_key=key,
        label=label,
        value_type=value_type,
        canonical_unit=unit,
        produced_by=tool,
        remediation_default=disposition,
        remediation_tool=remediation_tool,
    )


_MEASUREMENTS = (
    _entry("package.file_present[role]", "required package file", MeasurementValueType.BOOL, None, "scan_package"),
    _entry(
        "package.filename[role]",
        "delivery filename",
        MeasurementValueType.STRING,
        None,
        "scan_package",
        AutomationDisposition.AUTO_REMEDIATE,
        "rename_delivery_copy",
    ),
    _entry(
        "package.manifest_present",
        "checksum manifest",
        MeasurementValueType.BOOL,
        None,
        "scan_package",
        AutomationDisposition.AUTO_REMEDIATE,
        "write_checksum_manifest",
    ),
    _entry("package.checksums_match", "package checksums", MeasurementValueType.BOOL, None, "verify_manifest"),
    _entry("container.format", "container format", MeasurementValueType.ENUM, None, "probe_media"),
    _entry("video.codec", "video codec", MeasurementValueType.ENUM, None, "probe_media"),
    _entry("video.width", "video width", MeasurementValueType.INT, "px", "probe_media"),
    _entry("video.height", "video height", MeasurementValueType.INT, "px", "probe_media"),
    _entry("video.frame_rate", "video frame rate", MeasurementValueType.RATIONAL, "fps", "probe_media"),
    _entry("video.scan_type", "video scan type", MeasurementValueType.ENUM, None, "probe_media"),
    _entry("audio.channel_count", "audio channel count", MeasurementValueType.INT, None, "probe_media"),
    _entry("audio.channel_layout", "audio channel layout", MeasurementValueType.ENUM, None, "probe_media"),
    _entry("audio.sample_rate", "audio sample rate", MeasurementValueType.INT, "Hz", "probe_media"),
    _entry(
        "audio.integrated_loudness",
        "integrated program loudness",
        MeasurementValueType.FLOAT,
        "LUFS",
        "measure_loudness",
        AutomationDisposition.HUMAN_APPROVAL_REQUIRED,
        "create_normalized_audio_derivative",
    ),
    _entry(
        "audio.true_peak",
        "audio true peak",
        MeasurementValueType.FLOAT,
        "dBTP",
        "measure_loudness",
        AutomationDisposition.HUMAN_APPROVAL_REQUIRED,
        "create_normalized_audio_derivative",
    ),
    _entry("audio.loudness_range", "audio loudness range", MeasurementValueType.FLOAT, "LU", "measure_loudness"),
    _entry("captions.present", "captions", MeasurementValueType.BOOL, None, "scan_package"),
    _entry(
        "captions.format",
        "caption format",
        MeasurementValueType.ENUM,
        None,
        "inspect_captions",
        AutomationDisposition.AUTO_REMEDIATE,
        "convert_caption_format",
    ),
    _entry("captions.cue_count", "caption cue count", MeasurementValueType.INT, None, "inspect_captions"),
    _entry("captions.first_cue_start", "first caption cue start", MeasurementValueType.FLOAT, "s", "inspect_captions"),
    _entry("captions.overlapping_cues", "overlapping caption cues", MeasurementValueType.INT, None, "inspect_captions"),
    _entry(
        "captions.max_cue_end_vs_duration",
        "last caption cue relative to duration",
        MeasurementValueType.FLOAT,
        "s",
        "inspect_captions",
    ),
)


def measurement_catalog() -> tuple[MeasurementDefinition, ...]:
    return _MEASUREMENTS


def get_measurement(measurement_key: str) -> MeasurementDefinition:
    for definition in _MEASUREMENTS:
        if definition.measurement_key == measurement_key:
            return definition
    raise KeyError(measurement_key)


def validate_measurement_value(
    measurement_key: str,
    value: CanonicalScalar,
    unit: str | None,
) -> None:
    """Reject facts that do not use the catalog's canonical representation."""

    try:
        definition = get_measurement(measurement_key)
    except KeyError as exc:
        raise ValueError(f"unknown measurement key: {measurement_key}") from exc
    if unit != definition.canonical_unit:
        raise ValueError(
            f"measurement must use canonical unit {definition.canonical_unit!r}"
        )

    value_type = definition.value_type
    valid = False
    if value_type is MeasurementValueType.BOOL:
        valid = type(value) is bool
    elif value_type is MeasurementValueType.INT:
        valid = type(value) is int
    elif value_type is MeasurementValueType.FLOAT:
        valid = type(value) is float and isfinite(value)
    elif value_type is MeasurementValueType.STRING:
        valid = type(value) is str and bool(value)
    elif value_type is MeasurementValueType.ENUM:
        valid = type(value) is str and bool(value) and value == value.casefold()
    elif value_type is MeasurementValueType.RATIONAL:
        if type(value) is str and re.fullmatch(r"[1-9][0-9]*/[1-9][0-9]*", value):
            valid = str(Fraction(value)) == value
    if not valid:
        raise ValueError(
            f"measurement value is not canonical for {measurement_key} "
            f"({value_type.value})"
        )


def allowed_constraint_operators(
    measurement_key: str,
) -> frozenset[ConstraintOperator]:
    """Return the closed operator subset for one catalog measurement."""

    value_type = get_measurement(measurement_key).value_type
    by_type = {
        MeasurementValueType.BOOL: frozenset(
            {
                ConstraintOperator.EQUALS,
                ConstraintOperator.PRESENT,
                ConstraintOperator.ABSENT,
            }
        ),
        MeasurementValueType.STRING: frozenset(
            {
                ConstraintOperator.EQUALS,
                ConstraintOperator.ONE_OF,
                ConstraintOperator.MATCHES_PATTERN,
            }
        ),
        MeasurementValueType.ENUM: frozenset(
            {ConstraintOperator.EQUALS, ConstraintOperator.ONE_OF}
        ),
        MeasurementValueType.INT: frozenset(
            {
                ConstraintOperator.EQUALS,
                ConstraintOperator.MIN,
                ConstraintOperator.MAX,
                ConstraintOperator.RANGE,
                ConstraintOperator.ONE_OF,
            }
        ),
        MeasurementValueType.FLOAT: frozenset(
            {
                ConstraintOperator.EQUALS,
                ConstraintOperator.MIN,
                ConstraintOperator.MAX,
                ConstraintOperator.RANGE,
                ConstraintOperator.ONE_OF,
            }
        ),
        MeasurementValueType.RATIONAL: frozenset(
            {ConstraintOperator.EQUALS, ConstraintOperator.ONE_OF}
        ),
    }
    return by_type[value_type]
