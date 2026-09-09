"""Deeply immutable primitives shared by AIRCheck domain contracts."""

from __future__ import annotations

from typing import Annotated, Mapping, TypeAlias

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RunId = Annotated[str, StringConstraints(pattern=r"^run_[a-z0-9_]+$")]
DocumentId = Annotated[str, StringConstraints(pattern=r"^doc_[a-z0-9_]+$")]
SegmentId = Annotated[
    str,
    StringConstraints(pattern=r"^doc_[a-z0-9_]+:s[0-9]{4}$"),
]
CandidateId = Annotated[str, StringConstraints(pattern=r"^candidate_[a-z0-9_]+$")]
AssetId = Annotated[str, StringConstraints(pattern=r"^asset_[a-z0-9_]+$")]
RequirementId = Annotated[str, StringConstraints(pattern=r"^req_[a-z0-9_]+$")]
CheckId = Annotated[str, StringConstraints(pattern=r"^check_[a-z0-9_]+$")]
PlanId = Annotated[str, StringConstraints(pattern=r"^plan_[a-z0-9_]+$")]
PlanItemId = Annotated[str, StringConstraints(pattern=r"^item_[a-z0-9_]+$")]
MeasurementId = Annotated[str, StringConstraints(pattern=r"^measurement_[a-z0-9_]+$")]
FindingId = Annotated[str, StringConstraints(pattern=r"^finding_[a-z0-9_]+$")]
EventId = Annotated[str, StringConstraints(pattern=r"^evt_[a-z0-9_]+$")]
DecisionId = Annotated[str, StringConstraints(pattern=r"^decision_[a-z0-9_]+$")]
AuthorizationId = Annotated[str, StringConstraints(pattern=r"^auth_[A-Za-z0-9_-]+$")]
ActionId = Annotated[str, StringConstraints(pattern=r"^action_[a-z0-9_]+$")]
EvidenceId = Annotated[str, StringConstraints(pattern=r"^evidence_[a-z0-9_]+$")]
ArtifactId = Annotated[str, StringConstraints(pattern=r"^artifact_[a-z0-9_]+$")]
OptionId = Annotated[str, StringConstraints(pattern=r"^option_[a-z0-9_]+$")]
ProfileId = Annotated[str, StringConstraints(pattern=r"^profile_[a-z0-9_]+$")]


class FrozenModel(BaseModel):
    """Strict, immutable base for values that cross trust boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)


CanonicalScalar: TypeAlias = str | int | float | bool | None


class CanonicalField(FrozenModel):
    """One scalar field in a deterministic, immutable object value."""

    key: NonEmptyStr
    value: CanonicalScalar


class CanonicalObject(FrozenModel):
    """Tuple-backed replacement for mutable JSON objects in domain state."""

    fields: tuple[CanonicalField, ...]

    @field_validator("fields")
    @classmethod
    def validate_unique_keys(
        cls,
        value: tuple[CanonicalField, ...],
    ) -> tuple[CanonicalField, ...]:
        keys = tuple(field.key for field in value)
        if len(keys) != len(set(keys)):
            raise ValueError("canonical object keys must be unique")
        return value

    @classmethod
    def from_mapping(cls, value: Mapping[str, CanonicalScalar]) -> CanonicalObject:
        return cls(
            fields=tuple(
                CanonicalField(key=key, value=item)
                for key, item in sorted(value.items())
            )
        )

    def get(self, key: str) -> CanonicalScalar:
        for field in self.fields:
            if field.key == key:
                return field.value
        raise KeyError(key)


CanonicalValue: TypeAlias = CanonicalScalar | CanonicalObject
