"""Deep-frozen compatibility schema for the preserved legacy M0 run fixture."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum

from pydantic import (
    Field,
    field_validator,
    model_validator,
)

from aircheck.domain.primitives import (
    AssetId,
    CanonicalObject,
    CanonicalScalar,
    CanonicalValue,
    DecisionId,
    EventId,
    EvidenceId,
    FindingId,
    FrozenModel,
    NonEmptyStr,
    RequirementId,
    RunId,
)
from aircheck.domain.status import RunStatus, TERMINAL_STATUSES


class ContractModel(FrozenModel):
    """Strict immutable base for auditable domain values."""


class RequirementCategory(str, Enum):
    PACKAGE = "PACKAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    CAPTIONS = "CAPTIONS"
    NAMING = "NAMING"
    METADATA = "METADATA"
    CHECKSUM = "CHECKSUM"


class RequirementSeverity(str, Enum):
    BLOCKER = "BLOCKER"
    WARNING = "WARNING"


class RemediationPolicy(str, Enum):
    INSPECT_ONLY = "INSPECT_ONLY"
    REVERSIBLE = "REVERSIBLE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    BLOCK = "BLOCK"


class FindingStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    FIXED = "FIXED"


class EventType(str, Enum):
    RUN_CREATED = "RUN_CREATED"
    INGESTION_STARTED = "INGESTION_STARTED"
    SPEC_INTERPRETED = "SPEC_INTERPRETED"
    QC_PLAN_CREATED = "QC_PLAN_CREATED"
    PACKAGE_INSPECTED = "PACKAGE_INSPECTED"
    VIDEO_INSPECTED = "VIDEO_INSPECTED"
    AUDIO_INSPECTED = "AUDIO_INSPECTED"
    CAPTIONS_INSPECTED = "CAPTIONS_INSPECTED"
    FINDINGS_READY = "FINDINGS_READY"
    LOUDNESS_REQUIREMENT_FAILED = "LOUDNESS_REQUIREMENT_FAILED"
    SAFE_REMEDIATION_APPLIED = "SAFE_REMEDIATION_APPLIED"
    HUMAN_DECISION_REQUIRED = "HUMAN_DECISION_REQUIRED"
    REMEDIATION_AUTHORIZED = "REMEDIATION_AUTHORIZED"
    DELIVERY_DERIVATIVE_CREATED = "DELIVERY_DERIVATIVE_CREATED"
    REVERIFICATION_STARTED = "REVERIFICATION_STARTED"
    DELIVERY_READY = "DELIVERY_READY"
    DELIVERY_BLOCKED = "DELIVERY_BLOCKED"


class Actor(str, Enum):
    SYSTEM = "SYSTEM"
    AIRCHECK = "AIRCHECK"
    HUMAN = "HUMAN"


class DecisionStatus(str, Enum):
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    REJECTED = "REJECTED"


class EvidenceKind(str, Enum):
    MEASUREMENT = "MEASUREMENT"
    TOOL_RESULT = "TOOL_RESULT"
    REPORT = "REPORT"
    MANIFEST = "MANIFEST"
    CHECKSUM = "CHECKSUM"
    LEDGER = "LEDGER"
    DERIVATIVE = "DERIVATIVE"


class AssetRef(ContractModel):
    asset_id: AssetId
    filename: NonEmptyStr
    role: NonEmptyStr
    media_type: NonEmptyStr
    version: int = Field(ge=1)
    is_original: bool
    derived_from: AssetId | None = None

    @model_validator(mode="after")
    def validate_derivative_provenance(self) -> AssetRef:
        if self.is_original and self.derived_from is not None:
            raise ValueError("original assets cannot declare derived_from")
        return self


class Requirement(ContractModel):
    requirement_id: RequirementId
    source_reference: NonEmptyStr
    category: RequirementCategory
    description: NonEmptyStr
    normalized_constraint: CanonicalObject
    applicable_assets: tuple[AssetId, ...] = Field(min_length=1)
    verification_tool: NonEmptyStr
    severity: RequirementSeverity
    remediation_policy: RemediationPolicy

    @field_validator("normalized_constraint", mode="before")
    @classmethod
    def freeze_constraint(
        cls,
        value: CanonicalObject | Mapping[str, CanonicalScalar],
    ) -> CanonicalObject:
        if isinstance(value, CanonicalObject):
            return value
        return CanonicalObject.from_mapping(value)

    @field_validator("applicable_assets")
    @classmethod
    def validate_unique_assets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("applicable_assets must be unique")
        return value


class EvidenceRef(ContractModel):
    evidence_id: EvidenceId
    kind: EvidenceKind
    label: NonEmptyStr
    uri: NonEmptyStr


class RemediationOption(ContractModel):
    tool_name: NonEmptyStr
    label: NonEmptyStr
    policy: RemediationPolicy


class Finding(ContractModel):
    finding_id: FindingId
    requirement_id: RequirementId
    asset_id: AssetId
    observed_value: CanonicalValue
    expected_value: CanonicalValue
    status: FindingStatus
    evidence: tuple[EvidenceRef, ...] = ()
    remediation_options: tuple[RemediationOption, ...] = ()

    @field_validator("observed_value", "expected_value", mode="before")
    @classmethod
    def freeze_value(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return CanonicalObject.from_mapping(value)
        return value


class HumanDecision(ContractModel):
    decision_id: DecisionId
    title: NonEmptyStr
    reason: NonEmptyStr
    tool_name: NonEmptyStr
    asset_id: AssetId
    status: DecisionStatus


class Event(ContractModel):
    event_id: EventId
    run_id: RunId
    timestamp: datetime
    event_type: EventType
    actor: Actor
    summary: NonEmptyStr
    evidence_refs: tuple[EvidenceId, ...] = ()

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class FinalEvidence(ContractModel):
    total_requirements: int = Field(ge=0)
    verified_requirements: int = Field(ge=0)
    resolved_findings: int = Field(ge=0)
    autonomous_remediations: int = Field(ge=0)
    authorized_remediations: int = Field(ge=0)
    unresolved_blockers: int = Field(ge=0)
    artifacts: tuple[EvidenceRef, ...] = ()

    @model_validator(mode="after")
    def validate_counts(self) -> FinalEvidence:
        if self.verified_requirements > self.total_requirements:
            raise ValueError("verified_requirements cannot exceed total_requirements")
        return self


class DeliveryRun(ContractModel):
    run_id: RunId
    program_id: NonEmptyStr
    destination_profile: NonEmptyStr
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    original_assets: tuple[AssetRef, ...]
    working_assets: tuple[AssetRef, ...]
    requirements: tuple[Requirement, ...]
    qc_plan: tuple[RequirementId, ...]
    findings: tuple[Finding, ...]
    pending_decision: HumanDecision | None = None
    events: tuple[Event, ...]
    final_evidence: FinalEvidence | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_run_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("run timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_run_consistency(self) -> DeliveryRun:
        self._validate_identifiers()
        self._validate_links()
        self._validate_ledger()
        self._validate_decision()
        self._validate_terminal_evidence()
        return self

    def _validate_identifiers(self) -> None:
        self._require_unique(
            [requirement.requirement_id for requirement in self.requirements],
            "duplicate requirement_id",
        )
        self._require_unique(
            [finding.finding_id for finding in self.findings],
            "duplicate finding_id",
        )
        self._require_unique(
            [event.event_id for event in self.events],
            "duplicate event_id",
        )
        self._require_unique(
            [asset.asset_id for asset in [*self.original_assets, *self.working_assets]],
            "duplicate asset_id",
        )

    def _validate_links(self) -> None:
        all_assets = [*self.original_assets, *self.working_assets]
        asset_ids = {asset.asset_id for asset in all_assets}
        original_ids = {asset.asset_id for asset in self.original_assets}
        requirement_ids = {requirement.requirement_id for requirement in self.requirements}

        if any(not asset.is_original for asset in self.original_assets):
            raise ValueError("original_assets must have is_original=true")
        if any(asset.is_original for asset in self.working_assets):
            raise ValueError("working_assets must have is_original=false")
        for asset in self.working_assets:
            if asset.derived_from is not None and asset.derived_from not in original_ids:
                raise ValueError("working asset derived_from must reference an original asset")

        for requirement in self.requirements:
            unknown_assets = set(requirement.applicable_assets) - asset_ids
            if unknown_assets:
                raise ValueError("requirement references unknown applicable asset")

        if len(self.qc_plan) != len(set(self.qc_plan)):
            raise ValueError("qc_plan requirement identifiers must be unique")
        if set(self.qc_plan) - requirement_ids:
            raise ValueError("qc_plan references unknown requirement_id")

        for finding in self.findings:
            if finding.requirement_id not in requirement_ids:
                raise ValueError("finding references unknown requirement_id")
            if finding.asset_id not in asset_ids:
                raise ValueError("finding references unknown asset_id")

        if self.pending_decision and self.pending_decision.asset_id not in asset_ids:
            raise ValueError("pending_decision references unknown asset_id")

    def _validate_ledger(self) -> None:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if any(event.run_id != self.run_id for event in self.events):
            raise ValueError("event run_id must match the delivery run")
        timestamps = [event.timestamp for event in self.events]
        if timestamps != sorted(timestamps):
            raise ValueError("events must remain chronological")
        if timestamps and timestamps[0] < self.created_at:
            raise ValueError("event timestamp cannot precede run creation")
        if timestamps and timestamps[-1] > self.updated_at:
            raise ValueError("event timestamp cannot follow updated_at")

        evidence_ids = {
            evidence.evidence_id
            for finding in self.findings
            for evidence in finding.evidence
        }
        if self.final_evidence:
            evidence_ids.update(
                artifact.evidence_id for artifact in self.final_evidence.artifacts
            )
        for event in self.events:
            if set(event.evidence_refs) - evidence_ids:
                raise ValueError("event references unknown evidence_id")

    def _validate_decision(self) -> None:
        if self.status is RunStatus.AWAITING_HUMAN_DECISION:
            if self.pending_decision is None:
                raise ValueError("pending_decision is required for this run state")
            if self.pending_decision.status is not DecisionStatus.PENDING:
                raise ValueError("pending_decision must have PENDING status")
        elif self.pending_decision is not None:
            raise ValueError("pending_decision is only valid when human authority is required")

    def _validate_terminal_evidence(self) -> None:
        if self.status is RunStatus.DELIVERY_READY:
            if self.final_evidence is None:
                raise ValueError("final_evidence is required for DELIVERY_READY")
            if self.final_evidence.unresolved_blockers:
                raise ValueError("DELIVERY_READY cannot contain unresolved blockers")
            if self.final_evidence.verified_requirements != self.final_evidence.total_requirements:
                raise ValueError("DELIVERY_READY requires every requirement to be verified")
            if self.final_evidence.total_requirements != len(self.requirements):
                raise ValueError("final_evidence total must match requirements")
            if any(finding.status is FindingStatus.FAIL for finding in self.findings):
                raise ValueError("DELIVERY_READY cannot contain failing findings")
        elif self.final_evidence is not None and self.status not in TERMINAL_STATUSES:
            raise ValueError("final_evidence is only valid for terminal run states")
        elif self.status is RunStatus.BLOCKED and self.final_evidence is not None:
            if self.final_evidence.unresolved_blockers == 0:
                raise ValueError("BLOCKED final_evidence requires an unresolved blocker")

    @staticmethod
    def _require_unique(values: Sequence[str], message: str) -> None:
        if len(values) != len(set(values)):
            raise ValueError(message)
