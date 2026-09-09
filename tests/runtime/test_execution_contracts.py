"""M4 coverage binding and S3 authority-hook contracts."""

from __future__ import annotations

from datetime import UTC, datetime

from aircheck.agent.authority_hook import (
    AuthorityHook,
    StrandsAuthorityHook,
    ToolCallDisposition,
)
from aircheck.authority import (
    AuthorityEngine,
    AuthorityRunContext,
    InMemoryAuthorityStore,
    PendingFinding,
)
from aircheck.domain.actions import RemediationOption
from aircheck.domain.admission import CandidateRequirement, ScopeDraft, admit
from aircheck.domain.assets import Asset
from aircheck.domain.constraints import ConstraintDraft
from aircheck.domain.planning import derive_required_checks
from aircheck.domain.source import ingest_source_document, segment_source_document
from aircheck.domain.status import RunStatus
from aircheck.domain.types import (
    AssetProtection,
    AssetRole,
    FindingLifecycle,
    NormalizationStatus,
    ProposedObligation,
    SourceKind,
)
from aircheck.tools import TOOL_REGISTRY


RUN_ID = "run_m4_contracts"


def _working_master() -> Asset:
    return Asset(
        asset_id="asset_working_master",
        run_id=RUN_ID,
        role=AssetRole.PROGRAM_MASTER,
        filename="master.mov",
        sha256="a" * 64,
        size_bytes=10,
        protection=AssetProtection.WORKING,
        predecessor="asset_original_master",
        relation="COPIED_FROM",
    )


def _admitted_manifest_requirement():
    document = ingest_source_document(
        doc_id="doc_m4_contracts",
        run_id=RUN_ID,
        kind=SourceKind.SPEC,
        title="M4 package contract",
        raw=b"A SHA-256 checksum manifest is REQUIRED.",
        ingested_at=datetime(2026, 9, 7, tzinfo=UTC),
    )
    segments = segment_source_document(document)
    candidate = CandidateRequirement(
        candidate_id="candidate_manifest_required",
        segment_ids=(segments[0].segment_id,),
        quote=segments[0].text,
        proposed_obligation=ProposedObligation.MUST,
        measurement_key="package.manifest_present",
        constraint_draft=ConstraintDraft(operator="PRESENT"),
        scope_draft=ScopeDraft(asset_role="MANIFEST"),
        proposed_status=NormalizationStatus.EXECUTABLE,
        reason="Manifest presence requirement.",
        confidence=1,
    )
    return admit(candidate, document, segments)


def test_run_scoped_check_survives_when_required_asset_role_is_absent() -> None:
    """Dropping a missing-component check would let an absent manifest vanish."""

    checks = derive_required_checks(
        (_admitted_manifest_requirement(),),
        (_working_master(),),
    )

    assert len(checks) == 1
    assert checks[0].tool == "scan_package"
    assert tuple((arg.name, arg.value) for arg in checks[0].tool_args) == (
        ("run_id", RUN_ID),
    )


def _tier_two_call():
    return TOOL_REGISTRY["create_normalized_audio_derivative"].input_model(
        asset_id="asset_working_master",
        option_id="option_normalize_audio",
        target_lufs=-24,
        tolerance=2,
        authorization_id="auth_not_issued",
    )


def _tier_two_context(option: RemediationOption) -> AuthorityRunContext:
    return AuthorityRunContext(
        run_id=RUN_ID,
        status=RunStatus.FINDINGS_READY,
        assets=(_working_master(),),
        findings=(
            PendingFinding(
                finding_id=option.finding_id,
                status=FindingLifecycle.OPEN,
            ),
        ),
        options=(option,),
    )


def test_s3_hook_turns_tier_two_call_into_typed_interrupt_without_io() -> None:
    """A Tier-2 agent call must pause; it must never reach the tool body."""

    payload = _tier_two_call()
    option = RemediationOption.from_call(
        option_id=payload.option_id,
        finding_id="finding_loudness",
        tool="create_normalized_audio_derivative",
        payload=payload,
        tier=2,
        description="Create an authorized normalized-audio derivative.",
        produces_derivative=True,
    )
    hook = AuthorityHook(AuthorityEngine(InMemoryAuthorityStore()))

    result = hook.evaluate_agent_call(
        tool="create_normalized_audio_derivative",
        payload=payload,
        run=_tier_two_context(option),
        option=option,
        now=datetime(2026, 9, 7, 22, 0, tzinfo=UTC),
    )

    assert result.disposition is ToolCallDisposition.INTERRUPT
    assert result.decision_request is not None
    assert result.decision_request.run_id == RUN_ID
    assert result.decision_request.option_id == option.option_id
    assert result.decision_request.status.value == "PENDING"


def test_s3_hook_refuses_argument_substitution_without_interrupt() -> None:
    """A model changing bound arguments must be refused, not escalated."""

    payload = _tier_two_call()
    option = RemediationOption.from_call(
        option_id=payload.option_id,
        finding_id="finding_loudness",
        tool="create_normalized_audio_derivative",
        payload=payload,
        tier=2,
        description="Create an authorized normalized-audio derivative.",
        produces_derivative=True,
    )
    tampered = payload.model_copy(update={"target_lufs": -16})

    result = AuthorityHook(
        AuthorityEngine(InMemoryAuthorityStore())
    ).evaluate_agent_call(
        tool="create_normalized_audio_derivative",
        payload=tampered,
        run=_tier_two_context(option),
        option=option,
        now=datetime(2026, 9, 7, 22, 0, tzinfo=UTC),
    )

    assert result.disposition is ToolCallDisposition.REFUSE
    assert result.reason == "OPTION_ARGUMENT_BINDING_MISMATCH"
    assert result.decision_request is None


class _FakeBeforeToolCallEvent:
    def __init__(self, payload, run, option) -> None:
        self.tool_use = {
            "name": "create_normalized_audio_derivative",
            "input": payload.model_dump(mode="json"),
        }
        self.invocation_state = {
            "aircheck_authority_context": run,
            "aircheck_remediation_options": (option,),
        }
        self.cancel_tool = False
        self.interrupts: list[tuple[str, object]] = []

    def interrupt(self, *, name: str, reason: object) -> None:
        self.interrupts.append((name, reason))


def test_strands_before_tool_call_adapter_emits_raw_typed_interrupt() -> None:
    payload = _tier_two_call()
    option = RemediationOption.from_call(
        option_id=payload.option_id,
        finding_id="finding_loudness",
        tool="create_normalized_audio_derivative",
        payload=payload,
        tier=2,
        description="Create an authorized normalized-audio derivative.",
        produces_derivative=True,
    )
    event = _FakeBeforeToolCallEvent(payload, _tier_two_context(option), option)

    StrandsAuthorityHook(AuthorityEngine(InMemoryAuthorityStore())).before_tool_call(
        event,
        now=datetime(2026, 9, 7, 22, 0, tzinfo=UTC),
    )

    assert event.cancel_tool is False
    assert len(event.interrupts) == 1
    name, reason = event.interrupts[0]
    assert name.startswith("aircheck_authority_decision_")
    assert reason["run_id"] == RUN_ID
    assert reason["option_id"] == option.option_id


def test_strands_before_tool_call_adapter_cancels_foreign_argument_call() -> None:
    payload = _tier_two_call()
    option = RemediationOption.from_call(
        option_id=payload.option_id,
        finding_id="finding_loudness",
        tool="create_normalized_audio_derivative",
        payload=payload,
        tier=2,
        description="Create an authorized normalized-audio derivative.",
        produces_derivative=True,
    )
    event = _FakeBeforeToolCallEvent(
        payload.model_copy(update={"asset_id": "asset_foreign"}),
        _tier_two_context(option),
        option,
    )

    StrandsAuthorityHook(AuthorityEngine(InMemoryAuthorityStore())).before_tool_call(
        event,
        now=datetime(2026, 9, 7, 22, 0, tzinfo=UTC),
    )

    assert event.cancel_tool is True
    assert event.interrupts == []
    assert event.invocation_state["aircheck_authority_refusal"] == "RUN_ASSET_REQUIRED"
