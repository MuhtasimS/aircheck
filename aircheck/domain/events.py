"""Append-only, product-safe ledger event contracts."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator

from aircheck.domain.primitives import (
    ActionId,
    AssetId,
    AuthorizationId,
    EventId,
    FrozenModel,
    NonEmptyStr,
    RunId,
)
from aircheck.domain.status import RunStatus


class LedgerEventType(str, Enum):
    STATE_TRANSITION = "STATE_TRANSITION"
    ACTION_STARTED = "ACTION_STARTED"
    ACTION_COMPLETED = "ACTION_COMPLETED"
    ACTION_FAILED = "ACTION_FAILED"


class StateTransitionPayload(FrozenModel):
    kind: Literal["STATE_TRANSITION"] = "STATE_TRANSITION"
    from_state: RunStatus
    to_state: RunStatus


class ActionStartedPayload(FrozenModel):
    kind: Literal["ACTION_STARTED"] = "ACTION_STARTED"
    action_id: ActionId
    tool: NonEmptyStr
    authorization_id: AuthorizationId | None


class ActionCompletedPayload(FrozenModel):
    kind: Literal["ACTION_COMPLETED"] = "ACTION_COMPLETED"
    action_id: ActionId
    new_asset_id: AssetId
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ActionFailedPayload(FrozenModel):
    kind: Literal["ACTION_FAILED"] = "ACTION_FAILED"
    action_id: ActionId
    reason: NonEmptyStr


EventPayload: TypeAlias = Annotated[
    StateTransitionPayload
    | ActionStartedPayload
    | ActionCompletedPayload
    | ActionFailedPayload,
    Field(discriminator="kind"),
]


class LedgerEvent(FrozenModel):
    event_id: EventId
    run_id: RunId
    seq: int = Field(ge=1)
    timestamp: datetime
    type: LedgerEventType
    actor: NonEmptyStr
    summary: NonEmptyStr
    payload: EventPayload
    refs: tuple[str, ...]
    prev_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamp must be timezone-aware")
        return value


def event_hash(event: LedgerEvent) -> str:
    return hashlib.sha256(event.model_dump_json().encode("utf-8")).hexdigest()
