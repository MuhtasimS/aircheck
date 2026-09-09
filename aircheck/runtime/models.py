"""Deep-frozen durable records owned by the M4 execution runtime."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from pydantic import Field, field_validator, model_validator

from aircheck.authority import Decision, DecisionRequest
from aircheck.domain.actions import RemediationOption
from aircheck.domain.assets import Asset
from aircheck.domain.constraints import Constraint
from aircheck.domain.primitives import (
    ActionId,
    AssetId,
    DocumentId,
    EvidenceId,
    FindingId,
    FrozenModel,
    NonEmptyStr,
    RequirementId,
    RunId,
    SegmentId,
)
from aircheck.domain.source import SourceDocument, SourceSpan
from aircheck.domain.terminal import Measurement
from aircheck.domain.types import (
    ApplicabilityStatus,
    ArtifactKind,
    AutomationDisposition,
    FindingLifecycle,
    NormalizationStatus,
    PredicateOutcome,
    Severity,
)


class RuntimeAsset(FrozenModel):
    asset: Asset
    relative_path: NonEmptyStr

    @field_validator("relative_path")
    @classmethod
    def require_safe_relative_path(cls, value: str) -> str:
        windows = PureWindowsPath(value)
        posix = PurePosixPath(value)
        if (
            windows.is_absolute()
            or posix.is_absolute()
            or ".." in windows.parts
            or ".." in posix.parts
        ):
            raise ValueError("runtime asset path must be workspace-relative")
        return value.replace("\\", "/")


class RuntimeProvenance(FrozenModel):
    """Durable copy of provenance already validated by the admission factory."""

    doc_id: DocumentId
    segment_ids: tuple[SegmentId, ...] = Field(min_length=1, max_length=3)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: NonEmptyStr
    quote: NonEmptyStr
    reference: NonEmptyStr | None = None

    @classmethod
    def from_source_span(cls, span: SourceSpan) -> RuntimeProvenance:
        return cls(**span.model_dump())


class RuntimeRequirement(FrozenModel):
    requirement_id: RequirementId
    measurement_key: NonEmptyStr | None
    verification_tool: NonEmptyStr | None
    stream: str | None = None
    asset_role: NonEmptyStr
    constraint: Constraint | None
    severity: Severity
    normalization_status: NormalizationStatus
    applicability_status: ApplicabilityStatus
    disposition: AutomationDisposition
    rendered_text: NonEmptyStr
    provenance: RuntimeProvenance
    status_reasons: tuple[NonEmptyStr, ...] = ()


class RuntimePredicate(FrozenModel):
    requirement_id: RequirementId
    measurement_id: NonEmptyStr
    cycle: int = Field(ge=0)
    outcome: PredicateOutcome


class RuntimeFinding(FrozenModel):
    finding_id: FindingId
    requirement_id: RequirementId
    asset_id: AssetId
    cycle_opened: int = Field(ge=0)
    status: FindingLifecycle
    options: tuple[RemediationOption, ...] = ()
    observed_rendered: NonEmptyStr
    expected_rendered: NonEmptyStr
    resolved_by_action: ActionId | None = None


class RuntimeEvidenceArtifact(FrozenModel):
    evidence_id: EvidenceId
    kind: ArtifactKind
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    relative_path: NonEmptyStr
    created_by_action: ActionId | None = None


class RuntimeRun(FrozenModel):
    run_id: RunId
    profile_id: NonEmptyStr
    content_type: NonEmptyStr
    source_documents: tuple[SourceDocument, ...] = ()
    assets: tuple[RuntimeAsset, ...]
    requirements: tuple[RuntimeRequirement, ...] = ()
    check_order: tuple[RequirementId, ...] = ()
    measurements: tuple[Measurement, ...] = ()
    predicates: tuple[RuntimePredicate, ...] = ()
    findings: tuple[RuntimeFinding, ...] = ()
    decision_requests: tuple[DecisionRequest, ...] = ()
    decisions: tuple[Decision, ...] = ()
    original_hashes: tuple[tuple[AssetId, str], ...]
    evidence: tuple[RuntimeEvidenceArtifact, ...] = ()
    autonomous_remediations: int = Field(default=0, ge=0)
    authorized_remediations: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_record(self) -> RuntimeRun:
        assets = tuple(item.asset for item in self.assets)
        asset_ids = tuple(asset.asset_id for asset in assets)
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("runtime asset IDs must be unique")
        if any(asset.run_id != self.run_id for asset in assets):
            raise ValueError("runtime assets must belong to the run")
        original_ids = {
            asset.asset_id for asset in assets if asset.protection.value == "ORIGINAL"
        }
        declared_originals = tuple(asset_id for asset_id, _ in self.original_hashes)
        if set(declared_originals) != original_ids:
            raise ValueError("original hash ledger must cover every ORIGINAL exactly once")
        if len(declared_originals) != len(set(declared_originals)):
            raise ValueError("original hash ledger must not repeat an asset")
        if any(
            len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
            for _, digest in self.original_hashes
        ):
            raise ValueError("original hashes must be lowercase SHA-256 values")
        return self

    def current_assets(self) -> tuple[RuntimeAsset, ...]:
        superseded = {
            item.asset.predecessor
            for item in self.assets
            if item.asset.predecessor is not None
        }
        return tuple(
            item
            for item in self.assets
            if item.asset.asset_id not in superseded
            and item.asset.protection.value != "ORIGINAL"
        )

    def option(self, option_id: str) -> RemediationOption:
        for finding in self.findings:
            for option in finding.options:
                if option.option_id == option_id:
                    return option
        raise KeyError(option_id)


__all__ = (
    "RuntimeAsset",
    "RuntimeEvidenceArtifact",
    "RuntimeFinding",
    "RuntimePredicate",
    "RuntimeProvenance",
    "RuntimeRequirement",
    "RuntimeRun",
)
