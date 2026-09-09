"""Public AIRCheck foundation domain contracts."""

from aircheck.domain.actions import Action, ActionContext, RemediationOption
from aircheck.domain.admission import (
    CandidateRequirement,
    ExecutableRequirement,
    Requirement,
    admit,
)
from aircheck.domain.assets import Asset
from aircheck.domain.events import LedgerEvent
from aircheck.domain.source import SourceDocument, SourceSegment, SourceSpan
from aircheck.domain.state_machine import (
    InvalidStateTransition,
    RunStateSnapshot,
    can_transition,
    transition,
)
from aircheck.domain.status import RunStatus, TERMINAL_STATUSES
from aircheck.domain.terminal import TerminalVerdict, compute

__all__ = [
    "Action",
    "ActionContext",
    "Asset",
    "CandidateRequirement",
    "ExecutableRequirement",
    "InvalidStateTransition",
    "LedgerEvent",
    "RemediationOption",
    "Requirement",
    "RunStateSnapshot",
    "RunStatus",
    "SourceDocument",
    "SourceSegment",
    "SourceSpan",
    "TERMINAL_STATUSES",
    "TerminalVerdict",
    "admit",
    "can_transition",
    "compute",
    "transition",
]
