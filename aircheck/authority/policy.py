"""State-aware authority engine and opaque single-use authorization capabilities."""

from __future__ import annotations

import secrets
import threading
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, Field

from aircheck.domain.actions import (
    Action,
    ActionContext,
    RemediationOption,
    canonical_args_hash,
)
from aircheck.domain.assets import Asset
from aircheck.domain.primitives import (
    ActionId,
    AuthorizationId,
    DecisionId,
    FindingId,
    FrozenModel,
    NonEmptyStr,
    OptionId,
    RunId,
)
from aircheck.domain.state_machine import AuthorizationBinding, RunStateSnapshot
from aircheck.domain.status import RunStatus
from aircheck.domain.types import (
    ActionActor,
    AssetProtection,
    AuthorizationStatus,
    AuthorityTier,
    DecisionChoice,
    DecisionKind,
    DecisionRequestStatus,
    FindingLifecycle,
)
from aircheck.tools import TOOL_REGISTRY


class AuthorityError(ValueError):
    pass


class AuthorityOutcome(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_DECISION = "REQUIRE_DECISION"
    FORBID = "FORBID"


class AuthorityDecision(FrozenModel):
    outcome: AuthorityOutcome
    tier: AuthorityTier
    reason: NonEmptyStr


class PendingFinding(FrozenModel):
    finding_id: FindingId
    status: FindingLifecycle


class DecisionRequest(FrozenModel):
    decision_id: DecisionId
    run_id: RunId
    kind: DecisionKind
    finding_id: FindingId
    option_id: OptionId
    question_rendered: NonEmptyStr
    consequences_rendered: NonEmptyStr
    created_at: datetime
    status: DecisionRequestStatus


class Decision(FrozenModel):
    decision_id: DecisionId
    actor: NonEmptyStr
    choice: DecisionChoice
    decided_at: datetime


class Authorization(AuthorizationBinding):
    issued_at: datetime
    consumed_at: datetime | None = None
    consumed_by_action_id: ActionId | None = None
    voided_at: datetime | None = None
    void_reason: str | None = None


class ApprovalResult(FrozenModel):
    request: DecisionRequest
    decision: Decision
    authorization: Authorization | None


class AuthorityRunContext(FrozenModel):
    run_id: RunId
    status: RunStatus
    assets: tuple[Asset, ...]
    findings: tuple[PendingFinding, ...]
    options: tuple[RemediationOption, ...]
    pending_decision: DecisionRequest | None = None


class AuthorityStore(Protocol):
    def save(self, authorization: Authorization) -> None: ...

    def get(self, authorization_id: str) -> Authorization | None: ...

    def consume(
        self,
        authorization_id: str,
        *,
        run_id: str,
        option_id: str,
        args_hash: str,
        action_id: str | None = None,
    ) -> Authorization: ...

    def void(self, authorization_id: str, *, reason: str) -> Authorization: ...


class InMemoryAuthorityStore:
    def __init__(self, *, operation_log: list[str] | None = None) -> None:
        self._authorizations: dict[str, Authorization] = {}
        self._lock = threading.Lock()
        self._operation_log = operation_log

    def save(self, authorization: Authorization) -> None:
        with self._lock:
            if authorization.authorization_id in self._authorizations:
                raise AuthorityError("authorization identifier already exists")
            self._authorizations[authorization.authorization_id] = authorization
            if self._operation_log is not None:
                self._operation_log.append("AUTHORIZATION_ISSUED")

    def get(self, authorization_id: str) -> Authorization | None:
        with self._lock:
            return self._authorizations.get(authorization_id)

    def consume(
        self,
        authorization_id: str,
        *,
        run_id: str,
        option_id: str,
        args_hash: str,
        action_id: str | None = None,
    ) -> Authorization:
        with self._lock:
            authorization = self._authorizations.get(authorization_id)
            if authorization is None:
                raise AuthorityError("authorization does not exist")
            if authorization.status is not AuthorizationStatus.ISSUED:
                raise AuthorityError("authorization must be ISSUED")
            if (
                authorization.run_id,
                authorization.option_id,
                authorization.args_hash,
            ) != (run_id, option_id, args_hash):
                raise AuthorityError("authorization binding mismatch")
            consumed = authorization.model_copy(
                update={
                    "status": AuthorizationStatus.CONSUMED,
                    "consumed_at": datetime.now(UTC),
                    "consumed_by_action_id": action_id,
                }
            )
            self._authorizations[authorization_id] = consumed
            if self._operation_log is not None:
                self._operation_log.append("AUTHORIZATION_CONSUMED")
            return consumed

    def void(self, authorization_id: str, *, reason: str) -> Authorization:
        with self._lock:
            authorization = self._authorizations.get(authorization_id)
            if authorization is None:
                raise AuthorityError("authorization does not exist")
            if authorization.status is AuthorizationStatus.VOID:
                return authorization
            voided = authorization.model_copy(
                update={
                    "status": AuthorizationStatus.VOID,
                    "voided_at": datetime.now(UTC),
                    "void_reason": reason,
                }
            )
            self._authorizations[authorization_id] = voided
            if self._operation_log is not None:
                self._operation_log.append("AUTHORIZATION_VOIDED")
            return voided


class AuthorityEngine:
    def __init__(self, store: AuthorityStore) -> None:
        self._store = store

    def evaluate(
        self,
        tool: str,
        payload: BaseModel,
        run: AuthorityRunContext,
    ) -> AuthorityDecision:
        specification = TOOL_REGISTRY.get(tool)
        if specification is None:
            return AuthorityDecision(
                outcome=AuthorityOutcome.FORBID,
                tier=AuthorityTier.CONTENT_AFFECTING,
                reason="UNKNOWN_TOOL",
            )
        tier = specification.authority_tier
        if not isinstance(payload, specification.input_model):
            return AuthorityDecision(outcome=AuthorityOutcome.FORBID, tier=tier, reason="INVALID_TOOL_INPUT")
        payload_run_id = getattr(payload, "run_id", None)
        if payload_run_id is not None and payload_run_id != run.run_id:
            return AuthorityDecision(
                outcome=AuthorityOutcome.FORBID,
                tier=tier,
                reason="RUN_SCOPE_MISMATCH",
            )
        asset_id = getattr(payload, "asset_id", None)
        target = next((asset for asset in run.assets if asset.asset_id == asset_id), None)
        if asset_id is not None and (
            target is None or target.run_id != run.run_id
        ):
            return AuthorityDecision(
                outcome=AuthorityOutcome.FORBID,
                tier=tier,
                reason="RUN_ASSET_REQUIRED",
            )
        if tier is AuthorityTier.INSPECT:
            allowed = run.status in {RunStatus.INSPECTING, RunStatus.FINDINGS_READY}
            return AuthorityDecision(
                outcome=AuthorityOutcome.ALLOW if allowed else AuthorityOutcome.FORBID,
                tier=tier,
                reason="TIER0_INSPECTION_ALLOWED" if allowed else "INVALID_RUN_STATE",
            )

        if target is not None and target.protection is AssetProtection.ORIGINAL:
            return AuthorityDecision(
                outcome=AuthorityOutcome.FORBID,
                tier=tier,
                reason="ORIGINAL_TARGET_FORBIDDEN",
            )
        option_id = getattr(payload, "option_id", None)
        option = next((item for item in run.options if item.option_id == option_id), None)
        if option is None:
            return AuthorityDecision(outcome=AuthorityOutcome.FORBID, tier=tier, reason="OPEN_OPTION_REQUIRED")
        finding = next((item for item in run.findings if item.finding_id == option.finding_id), None)
        if finding is None or finding.status is not FindingLifecycle.OPEN:
            return AuthorityDecision(outcome=AuthorityOutcome.FORBID, tier=tier, reason="OPEN_FINDING_REQUIRED")
        if option.tool != tool or option.tier != int(tier):
            return AuthorityDecision(outcome=AuthorityOutcome.FORBID, tier=tier, reason="OPTION_TOOL_TIER_MISMATCH")
        if option.args_hash != canonical_args_hash(payload):
            return AuthorityDecision(outcome=AuthorityOutcome.FORBID, tier=tier, reason="OPTION_ARGUMENT_BINDING_MISMATCH")

        if tier is AuthorityTier.REVERSIBLE:
            allowed = run.status is RunStatus.FINDINGS_READY
            return AuthorityDecision(
                outcome=AuthorityOutcome.ALLOW if allowed else AuthorityOutcome.FORBID,
                tier=tier,
                reason="BOUND_TIER1_OPTION_ALLOWED" if allowed else "INVALID_RUN_STATE",
            )
        if run.status is RunStatus.FINDINGS_READY:
            return AuthorityDecision(
                outcome=AuthorityOutcome.REQUIRE_DECISION,
                tier=tier,
                reason="TIER2_DECISION_REQUIRED",
            )
        return AuthorityDecision(outcome=AuthorityOutcome.FORBID, tier=tier, reason="INVALID_RUN_STATE")


def create_decision_request(
    run: AuthorityRunContext,
    option: RemediationOption,
    *,
    now: datetime,
) -> DecisionRequest:
    if run.pending_decision is not None:
        raise AuthorityError("run already has a pending decision")
    if option not in run.options or option.tier != 2:
        raise AuthorityError("decision request requires a bound Tier-2 option")
    return DecisionRequest(
        decision_id=f"decision_{secrets.token_hex(10)}",
        run_id=run.run_id,
        kind=DecisionKind.TIER2_APPROVAL,
        finding_id=option.finding_id,
        option_id=option.option_id,
        question_rendered="Create the proposed content-affecting delivery derivative?",
        consequences_rendered="The original remains unchanged; one exact attempt is authorized.",
        created_at=now,
        status=DecisionRequestStatus.PENDING,
    )


def approve_decision(
    request: DecisionRequest,
    option: RemediationOption,
    *,
    actor: str,
    store: AuthorityStore,
    now: datetime | None = None,
) -> ApprovalResult:
    if request.status is not DecisionRequestStatus.PENDING:
        raise AuthorityError("decision request must be PENDING")
    if not actor.startswith("human:"):
        raise AuthorityError("decision actor must identify a human")
    if (request.option_id, request.finding_id) != (option.option_id, option.finding_id):
        raise AuthorityError("decision option binding mismatch")
    decided_at = now or datetime.now(UTC)
    decision = Decision(
        decision_id=request.decision_id,
        actor=actor,
        choice=DecisionChoice.APPROVED,
        decided_at=decided_at,
    )
    authorization = Authorization(
        authorization_id=f"auth_{secrets.token_urlsafe(24)}",
        run_id=request.run_id,
        decision_id=request.decision_id,
        option_id=option.option_id,
        args_hash=option.args_hash,
        status=AuthorizationStatus.ISSUED,
        issued_at=decided_at,
    )
    store.save(authorization)
    return ApprovalResult(
        request=request.model_copy(update={"status": DecisionRequestStatus.APPROVED}),
        decision=decision,
        authorization=authorization,
    )


class ActionStartedSink(Protocol):
    def append_action_started(self, action: Action) -> None: ...


class ActionExecutor:
    def __init__(self, store: AuthorityStore, sink: ActionStartedSink) -> None:
        self._store = store
        self._sink = sink

    def begin(
        self,
        *,
        run: RunStateSnapshot,
        authority_context: AuthorityRunContext,
        tool: str,
        payload: BaseModel,
        option: RemediationOption,
        actor: str,
    ) -> ActionContext:
        if run.status is not RunStatus.REMEDIATING:
            raise AuthorityError("actions may begin only in REMEDIATING")
        if authority_context.run_id != run.run_id:
            raise AuthorityError("authority context belongs to another run")
        specification = TOOL_REGISTRY.get(tool)
        if specification is None or specification.authority_tier is AuthorityTier.INSPECT:
            raise AuthorityError("ActionExecutor requires a mutating ToolSpec")
        payload_run_id = getattr(payload, "run_id", None)
        if payload_run_id is not None and payload_run_id != run.run_id:
            raise AuthorityError("action payload belongs to another run")
        if option not in authority_context.options or option.tool != tool:
            raise AuthorityError("action option is not bound to this run and tool")
        args_hash = canonical_args_hash(payload)
        if args_hash != option.args_hash or getattr(payload, "option_id", None) != option.option_id:
            raise AuthorityError("action argument binding mismatch")
        target_id = getattr(payload, "asset_id", None)
        target = next((asset for asset in authority_context.assets if asset.asset_id == target_id), None)
        if target_id is not None and (
            target is None or target.run_id != run.run_id
        ):
            raise AuthorityError("action target is not an asset in this run")
        if target is not None and target.protection is AssetProtection.ORIGINAL:
            raise AuthorityError("ORIGINAL target forbidden")

        authorization_id: str | None = None
        if specification.authority_tier is AuthorityTier.CONTENT_AFFECTING:
            authorization_id = getattr(payload, "authorization_id", None)
            if authorization_id is None:
                raise AuthorityError("Tier-2 action requires authorization")
        action = Action(
            action_id=f"action_{secrets.token_hex(10)}",
            run_id=run.run_id,
            cycle=run.cycle,
            tool=tool,
            option_id=option.option_id,
            args_hash=args_hash,
            authorization_id=authorization_id,
            actor=ActionActor(actor),
        )
        if specification.authority_tier is AuthorityTier.CONTENT_AFFECTING:
            begin_authorized = getattr(self._sink, "begin_authorized_action", None)
            if callable(begin_authorized) and self._store is self._sink:
                begin_authorized(action)
            else:
                assert authorization_id is not None
                self._store.consume(
                    authorization_id,
                    run_id=run.run_id,
                    option_id=option.option_id,
                    args_hash=args_hash,
                    action_id=action.action_id,
                )
                try:
                    self._sink.append_action_started(action)
                except Exception:
                    self._store.void(
                        authorization_id,
                        reason="ACTION_START_NOT_DURABLE",
                    )
                    raise
        else:
            self._sink.append_action_started(action)
        return ActionContext(
            action=action,
            option=option,
            authorization_id=authorization_id,
        )
