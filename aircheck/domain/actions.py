"""Immutable remediation-option and action-attempt contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, model_validator

from aircheck.domain.primitives import (
    ActionId,
    AuthorizationId,
    CanonicalScalar,
    FindingId,
    FrozenModel,
    NonEmptyStr,
    OptionId,
    RunId,
)
from aircheck.domain.types import ActionActor, AutomationDisposition


class CanonicalArgument(FrozenModel):
    name: NonEmptyStr
    value: CanonicalScalar


def canonical_arguments(payload: BaseModel) -> tuple[CanonicalArgument, ...]:
    values = payload.model_dump(mode="json", exclude={"authorization_id"})
    return tuple(
        CanonicalArgument(name=name, value=value)
        for name, value in sorted(values.items())
    )


def canonical_args_hash(
    value: BaseModel | tuple[CanonicalArgument, ...],
) -> str:
    arguments = canonical_arguments(value) if isinstance(value, BaseModel) else value
    encoded = json.dumps(
        tuple((argument.name, argument.value) for argument in arguments),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RemediationOption(FrozenModel):
    option_id: OptionId
    finding_id: FindingId
    tool: NonEmptyStr
    tool_args: tuple[CanonicalArgument, ...]
    args_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tier: int = Field(ge=1, le=2)
    disposition: AutomationDisposition
    description_rendered: NonEmptyStr
    produces_derivative: bool

    @model_validator(mode="after")
    def validate_binding_hash(self) -> RemediationOption:
        if canonical_args_hash(self.tool_args) != self.args_hash:
            raise ValueError("args_hash must bind the canonical tool arguments")
        return self

    @classmethod
    def from_call(
        cls,
        *,
        option_id: str,
        finding_id: str,
        tool: str,
        payload: BaseModel,
        tier: int,
        description: str,
        produces_derivative: bool,
    ) -> RemediationOption:
        arguments = canonical_arguments(payload)
        disposition = (
            AutomationDisposition.HUMAN_APPROVAL_REQUIRED
            if tier == 2
            else AutomationDisposition.AUTO_REMEDIATE
        )
        return cls(
            option_id=option_id,
            finding_id=finding_id,
            tool=tool,
            tool_args=arguments,
            args_hash=canonical_args_hash(arguments),
            tier=tier,
            disposition=disposition,
            description_rendered=description,
            produces_derivative=produces_derivative,
        )


class Action(FrozenModel):
    action_id: ActionId
    run_id: RunId
    cycle: int = Field(ge=0)
    tool: NonEmptyStr
    option_id: OptionId
    args_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorization_id: AuthorizationId | None = None
    actor: ActionActor


class ActionContext(FrozenModel):
    action: Action
    option: RemediationOption
    authorization_id: AuthorizationId | None = None
