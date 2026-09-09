"""Typed Pydantic projection models for the AIRCheck API."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class StatusMeta(BaseModel):
    name: str
    label: str
    badge_variant: str
    terminal: bool
    description: str


class ProfileMeta(BaseModel):
    profile_id: str
    profile_name: str
    description: str
    checks_count: int


class MetaStatesResponse(BaseModel):
    run_statuses: list[StatusMeta]
    product_statuses: list[StatusMeta]
    terminal_outcomes: list[str]
    profiles: list[ProfileMeta]


class TerminalVerdictProjection(BaseModel):
    outcome: str
    cycle: int
    reasons: list[str] = Field(default_factory=list)
    computed_at: str
    originals_integrity_verified: bool


class DeliverySummary(BaseModel):
    run_id: str
    program_id: str
    program_title: str
    destination_profile: str
    profile_id: str
    run_state: str
    display_status: str
    finding_summary: str
    verified: int
    total: int
    updated_at: str
    terminal_verdict: TerminalVerdictProjection | None = None


class DeliveriesCounts(BaseModel):
    active_runs: int
    decision_required: int
    delivery_ready: int
    unresolved_blockers: int


class DeliveriesResponse(BaseModel):
    runs: list[DeliverySummary]
    counts: DeliveriesCounts


class CreateRunRequest(BaseModel):
    profile_id: str = "northstar_broadcast_master_v1"
    package_type: Literal["hero", "clean"] = "hero"
    program_id: str = "the_last_lightkeeper"
    program_title: str = "The Last Lightkeeper"


class CreateRunResponse(BaseModel):
    run_id: str
    status: str
    profile_id: str
    profile_name: str
    message: str


class ProvenanceProjection(BaseModel):
    quote: str
    document_title: str
    line_start: int | None = None
    line_end: int | None = None


class RequirementView(BaseModel):
    id: str
    category: str
    description: str
    observed: str
    expected: str
    status: str
    severity: str
    predicate_outcome: str | None = None
    source_reference: str
    provenance: ProvenanceProjection | None = None
    disposition: str


class TimelineEventView(BaseModel):
    id: str
    seq: int
    time: str
    type: str
    actor: str
    summary: str
    evidence: str | None = None


class AssetView(BaseModel):
    id: str
    filename: str
    role: str
    detail: str
    provenance: str
    status: str
    sha256: str | None = None


class PendingDecisionView(BaseModel):
    decision_id: str
    finding_id: str
    option_id: str | None = None
    status: str
    resolved: bool = False
    question: str
    consequences: str
    tool: str | None = None
    tier: int | None = None
    policy_tier: str
    asset_filename: str
    source_asset_role: str
    measured_value: str
    required_value: str
    delta: str | None = None
    proposed_operation: str
    original_preservation: str
    requirement_id: str
    requirement_text: str
    source_reference: str
    source_quote: str | None = None
    produces_derivative: bool = False
    decided_by: str | None = None
    decision_choice: str | None = None


class RunMetrics(BaseModel):
    requirements_total: int
    passing: int
    remediated: int
    pending: int
    failed: int


class RunDetailView(BaseModel):
    run_id: str
    program_id: str
    program_title: str
    destination_profile: str
    profile_id: str
    run_state: str
    display_status: str
    cycle: int
    terminal_verdict: TerminalVerdictProjection | None = None
    pending_decision: PendingDecisionView | None = None
    requirements: list[RequirementView]
    events: list[TimelineEventView]
    assets: list[AssetView]
    metrics: RunMetrics
    evidence_available: bool


class DecideRequest(BaseModel):
    approved: bool
    actor: str = "human:operator"


class DecideResponse(BaseModel):
    run_id: str
    decision_id: str
    status: str
    choice: str
    next_status: str
    message: str


class EvidenceArtifactView(BaseModel):
    name: str
    filename: str
    size_bytes: int
    meta: str
    download_url: str
    icon_type: str


class EvidenceVerificationRow(BaseModel):
    requirement_id: str
    category: str
    observed: str
    expected: str
    status: str


class EvidenceMetrics(BaseModel):
    findings_resolved: int
    autonomous_fixes: int
    human_authorized: int
    open_blockers: int


class AgentCoreInvocationRecord(BaseModel):
    """One durable, product-safe record of a bounded S3-diagnosis invocation.

    Evidence ABOUT model-agency participation — never authority-bearing. Carries no
    chain-of-thought, prompt, or credential: only an opaque correlation id, the
    model/runtime identity, dispositions, latency, and any fallback reason.
    """

    source: str  # AGENTCORE | FALLBACK
    session_id: str
    model_id: str
    runtime: str | None = None
    latency_ms: int | None = None
    findings: int | None = None
    dispositions: list[str] = []
    fallback_reason: str | None = None


class AgentCoreProvenance(BaseModel):
    """Durable provenance of the run's semantic (S3 diagnosis) participation.

    Lets a judge distinguish a genuine AgentCore recommendation from a deterministic
    fallback directly from the run's evidence package, without relying on CloudWatch.
    Non-authority-bearing: it does not affect measurements, predicate truth,
    authorization, or the terminal outcome.
    """

    semantic_source: str  # AGENTCORE if any invocation used AgentCore, else FALLBACK
    model_id: str
    runtime: str | None = None
    invocations: list[AgentCoreInvocationRecord] = []


class EvidenceResponse(BaseModel):
    run_id: str
    program_title: str
    destination_profile: str
    run_state: str
    terminal_verdict: TerminalVerdictProjection | None = None
    earned_at: str | None = None
    package_hash: str | None = None
    requirements_verified: str
    metrics: EvidenceMetrics
    artifacts: list[EvidenceArtifactView]
    verification_rows: list[EvidenceVerificationRow]
    agentcore_provenance: AgentCoreProvenance | None = None
