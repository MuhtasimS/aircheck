"""Deterministic authority boundary for every S3-originated tool call."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from aircheck.authority import (
    AuthorityEngine,
    AuthorityOutcome,
    AuthorityRunContext,
    DecisionRequest,
    create_decision_request,
)
from aircheck.domain.actions import RemediationOption
from aircheck.domain.primitives import FrozenModel, NonEmptyStr
from aircheck.tools import TOOL_REGISTRY


class ToolCallDisposition(str, Enum):
    ALLOW = "ALLOW"
    INTERRUPT = "INTERRUPT"
    REFUSE = "REFUSE"


class ToolCallAuthorityResult(FrozenModel):
    disposition: ToolCallDisposition
    reason: NonEmptyStr
    decision_request: DecisionRequest | None = None


class AuthorityHook:
    """Translate policy outcomes into allow/refuse/raw-interrupt semantics."""

    def __init__(self, engine: AuthorityEngine) -> None:
        self._engine = engine

    def evaluate_agent_call(
        self,
        *,
        tool: str,
        payload: BaseModel,
        run: AuthorityRunContext,
        option: RemediationOption | None = None,
        now: datetime,
    ) -> ToolCallAuthorityResult:
        decision = self._engine.evaluate(tool, payload, run)
        if decision.outcome is AuthorityOutcome.ALLOW:
            return ToolCallAuthorityResult(
                disposition=ToolCallDisposition.ALLOW,
                reason=decision.reason,
            )
        if decision.outcome is AuthorityOutcome.FORBID:
            return ToolCallAuthorityResult(
                disposition=ToolCallDisposition.REFUSE,
                reason=decision.reason,
            )
        if option is None:
            return ToolCallAuthorityResult(
                disposition=ToolCallDisposition.REFUSE,
                reason="BOUND_TIER2_OPTION_REQUIRED",
            )
        return ToolCallAuthorityResult(
            disposition=ToolCallDisposition.INTERRUPT,
            reason=decision.reason,
            decision_request=create_decision_request(run, option, now=now),
        )


class StrandsAuthorityHook:
    """Optional Strands BeforeToolCall adapter around the canonical engine."""

    def __init__(self, engine: AuthorityEngine) -> None:
        self._hook = AuthorityHook(engine)

    def register_hooks(self, registry: Any) -> None:
        try:
            from strands.hooks.events import BeforeToolCallEvent
        except ImportError as exc:  # pragma: no cover - optional provider dependency.
            raise RuntimeError("install AIRCheck's agent dependency group for Strands") from exc
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)

    def before_tool_call(
        self,
        event: Any,
        *,
        now: datetime | None = None,
    ) -> None:
        """Allow, cancel, or emit a raw typed interrupt before tool execution."""

        invocation_state = getattr(event, "invocation_state", None)
        tool_use = getattr(event, "tool_use", None)
        if not isinstance(invocation_state, dict) or not isinstance(tool_use, dict):
            self._refuse(event, invocation_state, "AUTHORITY_CONTEXT_REQUIRED")
            return
        tool = tool_use.get("name")
        raw_input = tool_use.get("input")
        specification = TOOL_REGISTRY.get(tool) if isinstance(tool, str) else None
        if specification is None or not isinstance(raw_input, dict):
            self._refuse(event, invocation_state, "UNKNOWN_OR_INVALID_TOOL_CALL")
            return
        try:
            payload = specification.input_model.model_validate(raw_input)
        except (TypeError, ValueError):
            self._refuse(event, invocation_state, "INVALID_TOOL_INPUT")
            return
        run = invocation_state.get("aircheck_authority_context")
        options = invocation_state.get("aircheck_remediation_options", ())
        if not isinstance(run, AuthorityRunContext) or not isinstance(options, tuple):
            self._refuse(event, invocation_state, "AUTHORITY_CONTEXT_REQUIRED")
            return
        option_id = getattr(payload, "option_id", None)
        option = next(
            (
                candidate
                for candidate in options
                if isinstance(candidate, RemediationOption)
                and candidate.option_id == option_id
            ),
            None,
        )
        result = self._hook.evaluate_agent_call(
            tool=tool,
            payload=payload,
            run=run,
            option=option,
            now=now or datetime.now(UTC),
        )
        if result.disposition is ToolCallDisposition.ALLOW:
            return
        if result.disposition is ToolCallDisposition.REFUSE:
            self._refuse(event, invocation_state, result.reason)
            return
        assert result.decision_request is not None
        request = result.decision_request
        event.interrupt(
            name=f"aircheck_authority_{request.decision_id}",
            reason=request.model_dump(mode="json"),
        )

    @staticmethod
    def _refuse(event: Any, invocation_state: object, reason: str) -> None:
        event.cancel_tool = True
        if isinstance(invocation_state, dict):
            invocation_state["aircheck_authority_refusal"] = reason


__all__ = (
    "AuthorityHook",
    "StrandsAuthorityHook",
    "ToolCallAuthorityResult",
    "ToolCallDisposition",
)
