"""Nine frozen agent-facing ToolSpecs and their implementation metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Final

from pydantic import Field, model_validator

from aircheck.domain.catalog import validate_measurement_value
from aircheck.domain.primitives import (
    ArtifactId,
    AssetId,
    AuthorizationId,
    CanonicalScalar,
    FrozenModel,
    NonEmptyStr,
    OptionId,
    RunId,
)
from aircheck.domain.types import AuthorityTier, MeasurementStatus


class ToolStatus(str, Enum):
    OK = "OK"
    ERROR = "ERROR"


class _ToolInput(FrozenModel):
    @model_validator(mode="after")
    def reject_path_syntax(self) -> _ToolInput:
        for value in self.__dict__.values():
            if not isinstance(value, str):
                continue
            windows = PureWindowsPath(value)
            posix = PurePosixPath(value)
            if (
                windows.is_absolute()
                or posix.is_absolute()
                or ".." in windows.parts
                or ".." in posix.parts
                or "/" in value
                or "\\" in value
            ):
                raise ValueError("tool input string contains forbidden path syntax")
        return self


class ScanPackageInput(_ToolInput):
    run_id: RunId


class ProbeMediaInput(_ToolInput):
    asset_id: AssetId


class MeasureLoudnessInput(_ToolInput):
    asset_id: AssetId
    stream: NonEmptyStr


class InspectCaptionsInput(_ToolInput):
    asset_id: AssetId
    program_duration_s: float | None = Field(default=None, ge=0)


class VerifyManifestInput(_ToolInput):
    run_id: RunId


class RenameDeliveryCopyInput(_ToolInput):
    asset_id: AssetId
    option_id: OptionId
    new_filename: NonEmptyStr


class WriteChecksumManifestInput(_ToolInput):
    run_id: RunId
    option_id: OptionId
    format: NonEmptyStr


class ConvertCaptionFormatInput(_ToolInput):
    asset_id: AssetId
    option_id: OptionId
    target_format: NonEmptyStr


class CreateNormalizedAudioDerivativeInput(_ToolInput):
    asset_id: AssetId
    option_id: OptionId
    target_lufs: float
    tolerance: float = Field(gt=0)
    authorization_id: AuthorizationId


class MeasurementValue(FrozenModel):
    asset_id: AssetId | None = None
    measurement_key: NonEmptyStr
    value: CanonicalScalar | None = None
    unit: str | None = None
    status: MeasurementStatus = MeasurementStatus.OK
    error: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_measurement_shape(self) -> MeasurementValue:
        if self.status is MeasurementStatus.ERROR:
            if self.value is not None:
                raise ValueError("ERROR measurement values cannot carry a value")
            return self
        if self.value is None:
            raise ValueError("OK measurement values require a canonical value")
        if self.error is not None:
            raise ValueError("OK measurement values cannot carry an error")
        validate_measurement_value(self.measurement_key, self.value, self.unit)
        return self


class InspectionResult(FrozenModel):
    status: ToolStatus
    measurements: tuple[MeasurementValue, ...] = ()
    evidence_refs: tuple[ArtifactId, ...] = ()
    error: str | None = None


class RemediationResult(FrozenModel):
    status: ToolStatus
    new_asset_id: AssetId | None = None
    evidence_refs: tuple[ArtifactId, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    authority_tier: AuthorityTier
    description: str
    input_model: type[_ToolInput]
    output_model: type[InspectionResult] | type[RemediationResult]
    read_write: str
    idempotent: bool
    failure_semantics: str
    implemented: bool = False


def _spec(
    name: str,
    tier: AuthorityTier,
    description: str,
    input_model: type[_ToolInput],
    output_model: type[InspectionResult] | type[RemediationResult],
    read_write: str,
    failure: str,
    *,
    implemented: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        authority_tier=tier,
        description=description,
        input_model=input_model,
        output_model=output_model,
        read_write=read_write,
        idempotent=True,
        failure_semantics=failure,
        implemented=implemented,
    )


_SPECS = (
    _spec("scan_package", AuthorityTier.INSPECT, "Enumerate package facts.", ScanPackageInput, InspectionResult, "R", "Unreadable package returns ERROR.", implemented=True),
    _spec("probe_media", AuthorityTier.INSPECT, "Inspect container and stream facts.", ProbeMediaInput, InspectionResult, "R", "Probe failure returns ERROR measurements.", implemented=True),
    _spec("measure_loudness", AuthorityTier.INSPECT, "Measure loudness and peaks.", MeasureLoudnessInput, InspectionResult, "R", "Missing audio returns ERROR.", implemented=True),
    _spec("inspect_captions", AuthorityTier.INSPECT, "Inspect caption structure and timing.", InspectCaptionsInput, InspectionResult, "R", "Unparseable timing returns ERROR.", implemented=True),
    _spec("verify_manifest", AuthorityTier.INSPECT, "Verify manifest hashes.", VerifyManifestInput, InspectionResult, "R", "Missing manifest is NOT_EVALUATED.", implemented=True),
    _spec("rename_delivery_copy", AuthorityTier.REVERSIBLE, "Create a renamed working successor.", RenameDeliveryCopyInput, RemediationResult, "W working/", "Name collision returns ERROR.", implemented=True),
    _spec("write_checksum_manifest", AuthorityTier.REVERSIBLE, "Write the current checksum manifest.", WriteChecksumManifestInput, RemediationResult, "W working/", "Write failure returns ERROR.", implemented=True),
    _spec("convert_caption_format", AuthorityTier.REVERSIBLE, "Create an equivalent caption successor.", ConvertCaptionFormatInput, RemediationResult, "W working/", "Lossy round trip returns ERROR.", implemented=True),
    _spec("create_normalized_audio_derivative", AuthorityTier.CONTENT_AFFECTING, "Create an authorized audio derivative.", CreateNormalizedAudioDerivativeInput, RemediationResult, "W derivatives/", "Authorization or render failure returns ERROR before promotion.", implemented=True),
)


TOOL_REGISTRY: Final = MappingProxyType({spec.name: spec for spec in _SPECS})


def invoke_stub(name: str, payload: _ToolInput) -> InspectionResult | RemediationResult:
    """Validate the typed seam and truthfully decline execution in R2."""

    specification = TOOL_REGISTRY[name]
    if not isinstance(payload, specification.input_model):
        raise TypeError(f"{name} requires {specification.input_model.__name__}")
    if specification.authority_tier is not AuthorityTier.INSPECT:
        raise PermissionError("mutating tools require an ActionContext")
    return specification.output_model(status=ToolStatus.ERROR, error="NOT_IMPLEMENTED_R2")


def invoke_action_stub(
    name: str,
    payload: _ToolInput,
    context: object | None,
) -> RemediationResult:
    """Refuse side-effect execution unless the pre-I/O action context exists."""

    from aircheck.domain.actions import ActionContext, canonical_args_hash

    specification = TOOL_REGISTRY[name]
    if specification.authority_tier is AuthorityTier.INSPECT:
        raise TypeError("inspection tools do not use ActionContext")
    if not isinstance(payload, specification.input_model):
        raise TypeError(f"{name} requires {specification.input_model.__name__}")
    if not isinstance(context, ActionContext):
        raise PermissionError("mutating tool body requires a validated ActionContext")
    if (
        context.action.tool != name
        or context.action.option_id != getattr(payload, "option_id", None)
        or context.action.args_hash != canonical_args_hash(payload)
    ):
        raise PermissionError("ActionContext does not match the requested call")
    return RemediationResult(status=ToolStatus.ERROR, error="NOT_IMPLEMENTED_R2")
