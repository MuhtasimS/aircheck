"""Write-once asset and lineage contracts."""

from __future__ import annotations

from pydantic import Field, model_validator

from aircheck.domain.primitives import ActionId, AssetId, FrozenModel, NonEmptyStr, RunId
from aircheck.domain.types import AssetProtection, AssetRelation, AssetRole


class Asset(FrozenModel):
    asset_id: AssetId
    run_id: RunId
    role: AssetRole
    filename: NonEmptyStr
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    protection: AssetProtection
    predecessor: AssetId | None = None
    relation: AssetRelation | None = None
    created_by_action: ActionId | None = None

    @model_validator(mode="after")
    def validate_lineage(self) -> Asset:
        lineage = (self.predecessor, self.relation, self.created_by_action)
        if self.protection is AssetProtection.ORIGINAL and any(lineage):
            raise ValueError("ORIGINAL assets cannot declare successor lineage")
        if (self.predecessor is None) != (self.relation is None):
            raise ValueError("asset predecessor and relation must be supplied together")
        return self
