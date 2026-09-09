"""R2.8 authority capability, binding, and pre-I/O ordering tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aircheck.authority import (
    ActionExecutor,
    AuthorityDecision,
    AuthorityEngine,
    AuthorityError,
    AuthorityOutcome,
    AuthorityRunContext,
    InMemoryAuthorityStore,
    PendingFinding,
    approve_decision,
    create_decision_request,
)
from aircheck.domain.actions import RemediationOption, canonical_args_hash
from aircheck.domain.assets import Asset
from aircheck.domain.state_machine import RunStateSnapshot
from aircheck.domain.types import (
    AssetProtection,
    AssetRole,
    AuthorityTier,
    FindingLifecycle,
)
from aircheck.domain.status import RunStatus
from aircheck.tools import TOOL_REGISTRY, invoke_action_stub, invoke_stub


def _asset(
    asset_id: str = "asset_working",
    protection: AssetProtection = AssetProtection.WORKING,
) -> Asset:
    return Asset(
        asset_id=asset_id,
        run_id="run_authority_001",
        role=AssetRole.PROGRAM_MASTER,
        filename="master.mov",
        sha256="a" * 64,
        size_bytes=100,
        protection=protection,
    )


def _payload(tool: str):
    values = {
        "scan_package": {"run_id": "run_authority_001"},
        "probe_media": {"asset_id": "asset_working"},
        "rename_delivery_copy": {
            "asset_id": "asset_working",
            "option_id": "option_rename",
            "new_filename": "PROGRAM_NBM.mov",
        },
        "create_normalized_audio_derivative": {
            "asset_id": "asset_working",
            "option_id": "option_audio",
            "target_lufs": -24,
            "tolerance": 2,
            "authorization_id": "auth_placeholder",
        },
    }
    return TOOL_REGISTRY[tool].input_model.model_validate(values[tool])


def _option(tool: str, payload, *, tier: int, finding_id: str = "finding_001") -> RemediationOption:
    return RemediationOption.from_call(
        option_id=str(payload.option_id),
        finding_id=finding_id,
        tool=tool,
        payload=payload,
        tier=tier,
        description="Bound remediation fixture.",
        produces_derivative=tier == 2,
    )


def _context(
    status: RunStatus,
    *,
    option: RemediationOption | None = None,
    asset: Asset | None = None,
) -> AuthorityRunContext:
    findings = ()
    options = ()
    if option:
        findings = (
            PendingFinding(
                finding_id=option.finding_id,
                status=FindingLifecycle.OPEN,
            ),
        )
        options = (option,)
    return AuthorityRunContext(
        run_id="run_authority_001",
        status=status,
        assets=(asset or _asset(),),
        findings=findings,
        options=options,
    )


@pytest.mark.parametrize("state", [RunStatus.INSPECTING, RunStatus.FINDINGS_READY])
def test_tier_zero_is_allowed_only_in_inspection_states(state: RunStatus) -> None:
    engine = AuthorityEngine(InMemoryAuthorityStore())
    decision = engine.evaluate("probe_media", _payload("probe_media"), _context(state))
    assert decision == AuthorityDecision(
        outcome=AuthorityOutcome.ALLOW,
        tier=AuthorityTier.INSPECT,
        reason="TIER0_INSPECTION_ALLOWED",
    )


def test_unknown_tool_and_wrong_state_are_denied() -> None:
    engine = AuthorityEngine(InMemoryAuthorityStore())
    assert engine.evaluate("invent_metadata", _payload("scan_package"), _context(RunStatus.INSPECTING)).outcome is AuthorityOutcome.FORBID
    assert engine.evaluate("probe_media", _payload("probe_media"), _context(RunStatus.CREATED)).outcome is AuthorityOutcome.FORBID


def test_authority_rejects_inputs_outside_the_current_run() -> None:
    engine = AuthorityEngine(InMemoryAuthorityStore())
    context = _context(RunStatus.INSPECTING)

    wrong_run = _payload("scan_package").model_copy(
        update={"run_id": "run_authority_other"}
    )
    unknown_asset = _payload("probe_media").model_copy(
        update={"asset_id": "asset_not_in_run"}
    )

    assert engine.evaluate("scan_package", wrong_run, context).reason == "RUN_SCOPE_MISMATCH"
    assert engine.evaluate("probe_media", unknown_asset, context).reason == "RUN_ASSET_REQUIRED"


def test_tier_one_requires_open_finding_and_exact_option_arguments() -> None:
    payload = _payload("rename_delivery_copy")
    option = _option("rename_delivery_copy", payload, tier=1)
    engine = AuthorityEngine(InMemoryAuthorityStore())

    allowed = engine.evaluate(
        "rename_delivery_copy",
        payload,
        _context(RunStatus.FINDINGS_READY, option=option),
    )
    wrong_args = payload.model_copy(update={"new_filename": "DIFFERENT.mov"})
    denied = engine.evaluate(
        "rename_delivery_copy",
        wrong_args,
        _context(RunStatus.FINDINGS_READY, option=option),
    )

    assert allowed.outcome is AuthorityOutcome.ALLOW
    assert denied.outcome is AuthorityOutcome.FORBID
    assert denied.reason == "OPTION_ARGUMENT_BINDING_MISMATCH"


@pytest.mark.parametrize("tier", [AuthorityTier.REVERSIBLE, AuthorityTier.CONTENT_AFFECTING])
def test_original_targets_are_forbidden_for_every_mutating_tier(tier: AuthorityTier) -> None:
    tool = (
        "rename_delivery_copy"
        if tier is AuthorityTier.REVERSIBLE
        else "create_normalized_audio_derivative"
    )
    payload = _payload(tool)
    option = _option(tool, payload, tier=int(tier))
    original = _asset("asset_working", AssetProtection.ORIGINAL)
    decision = AuthorityEngine(InMemoryAuthorityStore()).evaluate(
        tool,
        payload,
        _context(RunStatus.FINDINGS_READY, option=option, asset=original),
    )
    assert decision.outcome is AuthorityOutcome.FORBID
    assert decision.reason == "ORIGINAL_TARGET_FORBIDDEN"


def test_tier_two_requires_decision_then_mints_opaque_bound_capability() -> None:
    store = InMemoryAuthorityStore()
    engine = AuthorityEngine(store)
    payload = _payload("create_normalized_audio_derivative")
    option = _option("create_normalized_audio_derivative", payload, tier=2)
    context = _context(RunStatus.FINDINGS_READY, option=option)

    decision = engine.evaluate("create_normalized_audio_derivative", payload, context)
    assert decision.outcome is AuthorityOutcome.REQUIRE_DECISION

    request = create_decision_request(context, option, now=datetime(2026, 9, 4, 12, 0, tzinfo=UTC))
    first = approve_decision(request, option, actor="human:mu", store=store)
    second_store = InMemoryAuthorityStore()
    second = approve_decision(request, option, actor="human:mu", store=second_store)

    assert first.authorization is not None
    assert second.authorization is not None
    assert first.authorization.authorization_id != second.authorization.authorization_id
    assert first.authorization.args_hash == canonical_args_hash(payload)
    assert first.authorization.option_id == option.option_id
    assert option.args_hash not in first.authorization.authorization_id


def test_authorization_is_single_use_and_exactly_bound() -> None:
    store = InMemoryAuthorityStore()
    payload = _payload("create_normalized_audio_derivative")
    option = _option("create_normalized_audio_derivative", payload, tier=2)
    context = _context(RunStatus.FINDINGS_READY, option=option)
    request = create_decision_request(context, option, now=datetime.now(UTC))
    authorization = approve_decision(request, option, actor="human:mu", store=store).authorization
    assert authorization is not None

    with pytest.raises(AuthorityError, match="binding"):
        store.consume(
            authorization.authorization_id,
            run_id=context.run_id,
            option_id="option_wrong",
            args_hash=option.args_hash,
        )
    consumed = store.consume(
        authorization.authorization_id,
        run_id=context.run_id,
        option_id=option.option_id,
        args_hash=option.args_hash,
    )
    assert consumed.status.value == "CONSUMED"
    with pytest.raises(AuthorityError, match="ISSUED"):
        store.consume(
            authorization.authorization_id,
            run_id=context.run_id,
            option_id=option.option_id,
            args_hash=option.args_hash,
        )


def test_action_executor_consumes_tier_two_before_action_started_and_before_io(tmp_path: Path) -> None:
    ordering: list[str] = []
    store = InMemoryAuthorityStore(operation_log=ordering)
    payload = _payload("create_normalized_audio_derivative")
    option = _option("create_normalized_audio_derivative", payload, tier=2)
    context = _context(RunStatus.FINDINGS_READY, option=option)
    request = create_decision_request(context, option, now=datetime.now(UTC))
    authorization = approve_decision(request, option, actor="human:mu", store=store).authorization
    assert authorization is not None

    class Sink:
        def append_action_started(self, action) -> None:
            ordering.append("ACTION_STARTED")

    run = RunStateSnapshot(
        run_id=context.run_id,
        status=RunStatus.REMEDIATING,
        cycle=0,
    )
    action_context = ActionExecutor(store, Sink()).begin(
        run=run,
        authority_context=context,
        tool="create_normalized_audio_derivative",
        payload=payload.model_copy(
            update={"authorization_id": authorization.authorization_id}
        ),
        option=option,
        actor="SYSTEM",
    )

    assert ordering[-2:] == ["AUTHORIZATION_CONSUMED", "ACTION_STARTED"]
    before = tuple(tmp_path.rglob("*"))
    result = invoke_action_stub(
        "create_normalized_audio_derivative",
        payload,
        action_context,
    )
    assert result.error == "NOT_IMPLEMENTED_R2"
    assert tuple(tmp_path.rglob("*")) == before


def test_action_executor_rejects_authority_context_from_another_run() -> None:
    payload = _payload("rename_delivery_copy")
    option = _option("rename_delivery_copy", payload, tier=1)
    context = _context(RunStatus.FINDINGS_READY, option=option).model_copy(
        update={"run_id": "run_authority_other"}
    )
    run = RunStateSnapshot(
        run_id="run_authority_001",
        status=RunStatus.REMEDIATING,
        cycle=0,
    )

    class Sink:
        def append_action_started(self, action) -> None:
            raise AssertionError("foreign-run action must not start")

    with pytest.raises(AuthorityError, match="run"):
        ActionExecutor(InMemoryAuthorityStore(), Sink()).begin(
            run=run,
            authority_context=context,
            tool="rename_delivery_copy",
            payload=payload,
            option=option,
            actor="SYSTEM",
        )


def test_action_executor_rejects_target_outside_the_current_run() -> None:
    payload = _payload("rename_delivery_copy").model_copy(
        update={"asset_id": "asset_not_in_run"}
    )
    option = _option("rename_delivery_copy", payload, tier=1)
    context = _context(RunStatus.FINDINGS_READY, option=option)
    run = RunStateSnapshot(
        run_id=context.run_id,
        status=RunStatus.REMEDIATING,
        cycle=0,
    )

    class Sink:
        def append_action_started(self, action) -> None:
            raise AssertionError("out-of-run asset action must not start")

    with pytest.raises(AuthorityError, match="asset"):
        ActionExecutor(InMemoryAuthorityStore(), Sink()).begin(
            run=run,
            authority_context=context,
            tool="rename_delivery_copy",
            payload=payload,
            option=option,
            actor="SYSTEM",
        )


def test_action_executor_rejects_payload_from_another_run() -> None:
    tool = "write_checksum_manifest"
    payload = TOOL_REGISTRY[tool].input_model.model_validate(
        {
            "run_id": "run_authority_other",
            "option_id": "option_manifest",
            "format": "sha256",
        }
    )
    option = _option(tool, payload, tier=1)
    context = _context(RunStatus.FINDINGS_READY, option=option)
    run = RunStateSnapshot(
        run_id=context.run_id,
        status=RunStatus.REMEDIATING,
        cycle=0,
    )

    class Sink:
        def append_action_started(self, action) -> None:
            raise AssertionError("foreign-run payload action must not start")

    with pytest.raises(AuthorityError, match="run"):
        ActionExecutor(InMemoryAuthorityStore(), Sink()).begin(
            run=run,
            authority_context=context,
            tool=tool,
            payload=payload,
            option=option,
            actor="SYSTEM",
        )


def test_mutating_tool_body_refuses_direct_bypass() -> None:
    payload = _payload("rename_delivery_copy")
    with pytest.raises(PermissionError, match="ActionContext"):
        invoke_action_stub("rename_delivery_copy", payload, None)
    with pytest.raises(PermissionError, match="ActionContext"):
        invoke_stub("rename_delivery_copy", payload)
