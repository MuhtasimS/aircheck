"""Runtime-only workspace path oracle and original-asset protection."""

from __future__ import annotations

from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType

from pydantic import field_validator

from aircheck.domain.assets import Asset
from aircheck.domain.primitives import FrozenModel, NonEmptyStr
from aircheck.domain.types import AssetProtection


class PathIntent(str, Enum):
    READ = "READ"
    WRITE = "WRITE"


class RuntimeAssetPath(FrozenModel):
    asset: Asset
    relative_path: NonEmptyStr

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        windows = PureWindowsPath(value)
        posix = PurePosixPath(value)
        if windows.is_absolute() or posix.is_absolute() or ".." in windows.parts or ".." in posix.parts:
            raise ValueError("runtime asset path must be workspace-relative")
        return value


class WorkspaceResolver:
    def __init__(self, root: Path, assets: tuple[RuntimeAssetPath, ...]) -> None:
        self._root = root.resolve()
        self._assets = MappingProxyType({item.asset.asset_id: item for item in assets})

    def resolve(self, asset_id: str, intent: PathIntent) -> Path:
        try:
            record = self._assets[asset_id]
        except KeyError as exc:
            raise KeyError(f"unknown asset_id: {asset_id}") from exc
        if intent is PathIntent.WRITE and record.asset.protection is AssetProtection.ORIGINAL:
            raise PermissionError("ORIGINAL assets are read-only")
        candidate = (self._root / Path(record.relative_path)).resolve()
        if not candidate.is_relative_to(self._root):
            raise PermissionError("resolved path escapes the run workspace")
        if intent is PathIntent.WRITE:
            required_root = {
                AssetProtection.WORKING: self._root / "working",
                AssetProtection.DERIVATIVE: self._root / "derivatives",
            }[record.asset.protection].resolve()
            if not candidate.is_relative_to(required_root):
                raise PermissionError("write target is outside its protected workspace tree")
        return candidate
