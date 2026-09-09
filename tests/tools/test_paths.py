"""R2.7 path-oracle and original-protection tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aircheck.domain.assets import Asset
from aircheck.domain.types import AssetProtection, AssetRole
from aircheck.tools.contracts import RenameDeliveryCopyInput
from aircheck.tools.paths import PathIntent, RuntimeAssetPath, WorkspaceResolver


def _asset(protection: AssetProtection) -> Asset:
    return Asset(
        asset_id=f"asset_{protection.value.casefold()}",
        run_id="run_paths_001",
        role=AssetRole.PROGRAM_MASTER,
        filename="master.mov",
        sha256="a" * 64,
        size_bytes=100,
        protection=protection,
    )


@pytest.mark.parametrize(
    "filename",
    ("../escape.mov", "folder/escape.mov", r"C:\\escape.mov", "/tmp/escape.mov"),
)
def test_tool_string_inputs_reject_path_traversal(filename: str) -> None:
    with pytest.raises(ValidationError, match="path syntax"):
        RenameDeliveryCopyInput(
            asset_id="asset_working",
            option_id="option_rename",
            new_filename=filename,
        )


def test_runtime_asset_path_rejects_absolute_and_parent_escape() -> None:
    with pytest.raises(ValidationError):
        RuntimeAssetPath(
            asset=_asset(AssetProtection.WORKING),
            relative_path="../outside.mov",
        )
    with pytest.raises(ValidationError):
        RuntimeAssetPath(
            asset=_asset(AssetProtection.WORKING),
            relative_path="C:/outside.mov",
        )


def test_original_can_be_read_but_never_resolved_for_write(tmp_path: Path) -> None:
    original = RuntimeAssetPath(
        asset=_asset(AssetProtection.ORIGINAL),
        relative_path="originals/master.mov",
    )
    resolver = WorkspaceResolver(tmp_path, (original,))

    assert resolver.resolve(original.asset.asset_id, PathIntent.READ) == (
        tmp_path / "originals" / "master.mov"
    ).resolve()
    with pytest.raises(PermissionError, match="ORIGINAL"):
        resolver.resolve(original.asset.asset_id, PathIntent.WRITE)


def test_working_write_must_remain_inside_workspace_and_working_tree(tmp_path: Path) -> None:
    working = RuntimeAssetPath(
        asset=_asset(AssetProtection.WORKING),
        relative_path="working/master.mov",
    )
    resolver = WorkspaceResolver(tmp_path, (working,))

    assert resolver.resolve(working.asset.asset_id, PathIntent.WRITE) == (
        tmp_path / "working" / "master.mov"
    ).resolve()
