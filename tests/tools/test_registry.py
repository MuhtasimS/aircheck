"""R2.7 exact tool-inventory and typed-stub tests."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from aircheck.domain.types import AuthorityTier
from aircheck.tools import TOOL_REGISTRY, ToolStatus, invoke_stub


FROZEN_TOOL_NAMES = (
    "scan_package",
    "probe_media",
    "measure_loudness",
    "inspect_captions",
    "verify_manifest",
    "rename_delivery_copy",
    "write_checksum_manifest",
    "convert_caption_format",
    "create_normalized_audio_derivative",
)


VALID_INPUTS = {
    "scan_package": {"run_id": "run_tools_001"},
    "probe_media": {"asset_id": "asset_master"},
    "measure_loudness": {"asset_id": "asset_master", "stream": "primary_audio"},
    "inspect_captions": {"asset_id": "asset_captions", "program_duration_s": 90.0},
    "verify_manifest": {"run_id": "run_tools_001"},
    "rename_delivery_copy": {
        "asset_id": "asset_working",
        "option_id": "option_rename",
        "new_filename": "PROGRAM_NBM_20260904.mov",
    },
    "write_checksum_manifest": {
        "run_id": "run_tools_001",
        "option_id": "option_manifest",
        "format": "sha256sums",
    },
    "convert_caption_format": {
        "asset_id": "asset_captions",
        "option_id": "option_captions",
        "target_format": "webvtt",
    },
    "create_normalized_audio_derivative": {
        "asset_id": "asset_working",
        "option_id": "option_audio",
        "target_lufs": -24.0,
        "tolerance": 2.0,
        "authorization_id": "auth_runtime-opaque",
    },
}


def test_registry_contains_exactly_the_nine_agent_facing_tools() -> None:
    assert tuple(TOOL_REGISTRY) == FROZEN_TOOL_NAMES
    assert not {
        "verify_filename",
        "hash_assets",
        "request_human_decision",
        "finalize_delivery",
    } & set(TOOL_REGISTRY)


def test_every_tool_has_frozen_typed_io_and_declared_operational_metadata() -> None:
    for name, specification in TOOL_REGISTRY.items():
        assert specification.name == name
        assert issubclass(specification.input_model, BaseModel)
        assert issubclass(specification.output_model, BaseModel)
        assert specification.input_model.model_config.get("frozen") is True
        assert specification.output_model.model_config.get("frozen") is True
        assert specification.read_write in {"R", "W working/", "W derivatives/"}
        assert specification.idempotent is True
        assert specification.failure_semantics
        assert specification.implemented is True


def test_only_the_audio_derivative_is_tier_two() -> None:
    tier_two = tuple(
        name
        for name, specification in TOOL_REGISTRY.items()
        if specification.authority_tier is AuthorityTier.CONTENT_AFFECTING
    )
    assert tier_two == ("create_normalized_audio_derivative",)
    assert all(
        specification.authority_tier is AuthorityTier.INSPECT
        for specification in tuple(TOOL_REGISTRY.values())[:5]
    )
    assert all(
        specification.authority_tier is AuthorityTier.REVERSIBLE
        for specification in tuple(TOOL_REGISTRY.values())[5:8]
    )


def test_inputs_use_ids_not_paths_and_validate_real_payloads() -> None:
    for name, specification in TOOL_REGISTRY.items():
        assert not any(
            "path" in field_name.casefold()
            for field_name in specification.input_model.model_fields
        )
        assert specification.input_model.model_validate(VALID_INPUTS[name])


def test_mutating_tools_cannot_bypass_the_action_executor(tmp_path: Path) -> None:
    before = tuple(tmp_path.rglob("*"))
    for name, specification in tuple(TOOL_REGISTRY.items())[5:]:
        payload = specification.input_model.model_validate(VALID_INPUTS[name])
        try:
            invoke_stub(name, payload)
        except PermissionError as exc:
            assert "ActionContext" in str(exc)
        else:
            raise AssertionError("mutating stub accepted a direct call")
    assert tuple(tmp_path.rglob("*")) == before
