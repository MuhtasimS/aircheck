"""Frozen contracts crossing AIRCheck's bounded semantic-agent boundary."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Protocol

from pydantic import Field, field_validator

from aircheck.domain.admission import CandidateRequirement
from aircheck.domain.primitives import AssetId, FrozenModel, NonEmptyStr, RunId
from aircheck.domain.types import AssetRole


class InventoryAsset(FrozenModel):
    """Path-free package context exposed to S1."""

    asset_id: AssetId
    role: AssetRole
    filename: NonEmptyStr

    @field_validator("filename")
    @classmethod
    def reject_path_syntax(cls, value: str) -> str:
        if (
            PureWindowsPath(value).is_absolute()
            or PurePosixPath(value).is_absolute()
            or "/" in value
            or "\\" in value
            or value in {".", ".."}
        ):
            raise ValueError("inventory filename must not contain a filesystem path")
        return value


class PackageInventory(FrozenModel):
    """Typed M3 package fixture/context; it contains no media measurements."""

    run_id: RunId
    content_type: NonEmptyStr
    destination_profile: NonEmptyStr
    assets: tuple[InventoryAsset, ...] = Field(min_length=1)

    @field_validator("assets")
    @classmethod
    def require_unique_asset_ids(
        cls,
        value: tuple[InventoryAsset, ...],
    ) -> tuple[InventoryAsset, ...]:
        asset_ids = tuple(asset.asset_id for asset in value)
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("inventory asset IDs must be unique")
        return value


class CandidateRequirementBatch(FrozenModel):
    """The complete typed S1 response; candidates remain untrusted."""

    requirements: tuple[CandidateRequirement, ...] = Field(min_length=1)

    @field_validator("requirements")
    @classmethod
    def require_unique_candidate_ids(
        cls,
        value: tuple[CandidateRequirement, ...],
    ) -> tuple[CandidateRequirement, ...]:
        candidate_ids = tuple(candidate.candidate_id for candidate in value)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate IDs must be unique")
        return value


class StructuredSemanticModel(Protocol):
    """Provider-neutral local semantic invocation boundary."""

    def generate(self, prompt: str, output_model: type[FrozenModel]) -> Any:
        """Return a structured-output candidate for deterministic validation."""
