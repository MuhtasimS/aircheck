"""Frozen constraint language, normalization, and deterministic rendering."""

from __future__ import annotations

import re
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from aircheck.domain.catalog import MeasurementDefinition, allowed_constraint_operators
from aircheck.domain.primitives import CanonicalScalar, FrozenModel, NonEmptyStr
from aircheck.domain.types import ConstraintOperator, MeasurementValueType, NormalizationStatus


class ConstraintDraft(FrozenModel):
    operator: NonEmptyStr
    raw_values: tuple[str, ...] = ()
    raw_unit: str | None = None


class _ConstraintBase(FrozenModel):
    measurement_key: NonEmptyStr
    unit: NonEmptyStr | None = None


class EqualsConstraint(_ConstraintBase):
    operator: Literal[ConstraintOperator.EQUALS] = ConstraintOperator.EQUALS
    value: CanonicalScalar


class MinConstraint(_ConstraintBase):
    operator: Literal[ConstraintOperator.MIN] = ConstraintOperator.MIN
    value: CanonicalScalar
    inclusive: bool = True


class MaxConstraint(_ConstraintBase):
    operator: Literal[ConstraintOperator.MAX] = ConstraintOperator.MAX
    value: CanonicalScalar
    inclusive: bool = True


class RangeConstraint(_ConstraintBase):
    operator: Literal[ConstraintOperator.RANGE] = ConstraintOperator.RANGE
    lower: float | int
    upper: float | int
    inclusive_lower: bool = True
    inclusive_upper: bool = True
    target: float | int | None = None
    tolerance: float | int | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> RangeConstraint:
        if self.lower > self.upper:
            raise ValueError("range lower bound cannot exceed upper bound")
        return self


class OneOfConstraint(_ConstraintBase):
    operator: Literal[ConstraintOperator.ONE_OF] = ConstraintOperator.ONE_OF
    values: tuple[CanonicalScalar, ...] = Field(min_length=1)


class PatternConstraint(_ConstraintBase):
    operator: Literal[ConstraintOperator.MATCHES_PATTERN] = ConstraintOperator.MATCHES_PATTERN
    pattern: NonEmptyStr
    description: NonEmptyStr

    @model_validator(mode="after")
    def validate_pattern(self) -> PatternConstraint:
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValueError("pattern must compile") from exc
        return self


class PresentConstraint(_ConstraintBase):
    operator: Literal[ConstraintOperator.PRESENT] = ConstraintOperator.PRESENT


class AbsentConstraint(_ConstraintBase):
    operator: Literal[ConstraintOperator.ABSENT] = ConstraintOperator.ABSENT


Constraint: TypeAlias = Annotated[
    EqualsConstraint
    | MinConstraint
    | MaxConstraint
    | RangeConstraint
    | OneOfConstraint
    | PatternConstraint
    | PresentConstraint
    | AbsentConstraint,
    Field(discriminator="operator"),
]


_RATIONALS = {
    "23.976": "24000/1001",
    "29.97": "30000/1001",
    "59.94": "60000/1001",
}


def _canonical_unit(
    raw_unit: str | None,
    expected: str | None,
) -> tuple[str | None, float] | None:
    if expected is None:
        return (None, 1.0) if raw_unit is None or not raw_unit.strip() else None
    if raw_unit is None:
        return None
    compact = raw_unit.strip().casefold().replace(" ", "")
    aliases = {
        "LUFS": {"lufs": 1.0, "lkfs": 1.0},
        "dBTP": {"dbtp": 1.0, "dbfs(tp)": 1.0},
        "Hz": {"hz": 1.0, "khz": 1000.0},
        "fps": {"fps": 1.0},
        "px": {"px": 1.0, "pixel": 1.0, "pixels": 1.0},
        "LU": {"lu": 1.0, "db": 1.0},
        "s": {"s": 1.0, "sec": 1.0, "seconds": 1.0},
    }
    factor = aliases.get(expected, {}).get(compact)
    return None if factor is None else (expected, factor)


def _parse_value(
    raw: str,
    definition: MeasurementDefinition,
    factor: float,
) -> CanonicalScalar:
    value_type = definition.value_type
    stripped = raw.strip()
    if value_type is MeasurementValueType.BOOL:
        values = {"true": True, "yes": True, "present": True, "false": False, "no": False, "absent": False}
        return values[stripped.casefold()]
    if value_type is MeasurementValueType.INT:
        value = float(stripped) * factor
        if not value.is_integer():
            raise ValueError("integer measurement requires an integral value")
        return int(value)
    if value_type is MeasurementValueType.FLOAT:
        return float(stripped) * factor
    if value_type is MeasurementValueType.RATIONAL:
        return _RATIONALS.get(stripped, stripped)
    if value_type is MeasurementValueType.ENUM:
        normalized = stripped.casefold()
        aliases = {
            "video.codec": {
                "h.264": "h264",
            },
        }
        return aliases.get(definition.measurement_key, {}).get(normalized, normalized)
    return stripped


def normalize_constraint(
    draft: ConstraintDraft | None,
    definition: MeasurementDefinition,
) -> tuple[Constraint | None, NormalizationStatus, tuple[str, ...]]:
    if draft is None:
        return None, NormalizationStatus.AMBIGUOUS, ("MISSING_CONSTRAINT",)
    try:
        operator = ConstraintOperator(draft.operator)
    except ValueError:
        return None, NormalizationStatus.UNSUPPORTED, ("UNKNOWN_CONSTRAINT_OPERATOR",)
    if operator not in allowed_constraint_operators(definition.measurement_key):
        return None, NormalizationStatus.UNSUPPORTED, (
            "OPERATOR_NOT_SUPPORTED_FOR_MEASUREMENT",
        )

    unit_result = _canonical_unit(draft.raw_unit, definition.canonical_unit)
    if unit_result is None:
        return None, NormalizationStatus.AMBIGUOUS, ("UNKNOWN_UNIT",)
    unit, factor = unit_result

    expected_counts = {
        ConstraintOperator.EQUALS: (1, 1),
        ConstraintOperator.MIN: (1, 1),
        ConstraintOperator.MAX: (1, 1),
        ConstraintOperator.RANGE: (2, 2),
        ConstraintOperator.ONE_OF: (1, None),
        ConstraintOperator.MATCHES_PATTERN: (2, 2),
        ConstraintOperator.PRESENT: (0, 0),
        ConstraintOperator.ABSENT: (0, 0),
    }
    minimum, maximum = expected_counts[operator]
    if len(draft.raw_values) < minimum or (
        maximum is not None and len(draft.raw_values) > maximum
    ):
        reason = "MISSING_RANGE_VALUES" if operator is ConstraintOperator.RANGE else "INCOMPLETE_CONSTRAINT"
        return None, NormalizationStatus.AMBIGUOUS, (reason,)

    try:
        values = tuple(_parse_value(value, definition, factor) for value in draft.raw_values)
        common = {"measurement_key": definition.measurement_key, "unit": unit}
        if operator is ConstraintOperator.EQUALS:
            constraint: Constraint = EqualsConstraint(**common, value=values[0])
        elif operator is ConstraintOperator.MIN:
            constraint = MinConstraint(**common, value=values[0])
        elif operator is ConstraintOperator.MAX:
            constraint = MaxConstraint(**common, value=values[0])
        elif operator is ConstraintOperator.RANGE:
            constraint = RangeConstraint(**common, lower=values[0], upper=values[1])  # type: ignore[arg-type]
        elif operator is ConstraintOperator.ONE_OF:
            constraint = OneOfConstraint(**common, values=values)
        elif operator is ConstraintOperator.MATCHES_PATTERN:
            try:
                re.compile(str(values[0]))
            except re.error:
                return None, NormalizationStatus.AMBIGUOUS, ("INVALID_PATTERN",)
            constraint = PatternConstraint(
                **common,
                pattern=str(values[0]),
                description=str(values[1]),
            )
        elif operator is ConstraintOperator.PRESENT:
            constraint = PresentConstraint(**common)
        else:
            constraint = AbsentConstraint(**common)
    except (KeyError, TypeError, ValueError):
        return None, NormalizationStatus.AMBIGUOUS, ("INVALID_CONSTRAINT_VALUE",)
    return constraint, NormalizationStatus.EXECUTABLE, ()


def _display(value: CanonicalScalar) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render_constraint(constraint: Constraint, *, label: str) -> str:
    unit = f" {constraint.unit}" if constraint.unit else ""
    if isinstance(constraint, EqualsConstraint):
        return f"{label} to be {_display(constraint.value)}{unit}"
    if isinstance(constraint, MinConstraint):
        return f"{label} of at least {_display(constraint.value)}{unit}"
    if isinstance(constraint, MaxConstraint):
        return f"{label} of at most {_display(constraint.value)}{unit}"
    if isinstance(constraint, RangeConstraint):
        inclusive = " inclusive" if constraint.inclusive_lower and constraint.inclusive_upper else ""
        return f"{label} between {_display(constraint.lower)} and {_display(constraint.upper)}{unit}{inclusive}"
    if isinstance(constraint, OneOfConstraint):
        return f"{label} to be one of: {', '.join(_display(value) for value in constraint.values)}"
    if isinstance(constraint, PatternConstraint):
        return f'{label} to match the pattern "{constraint.description}"'
    if isinstance(constraint, PresentConstraint):
        return f"{label} to be present"
    return f"{label} to be absent"
