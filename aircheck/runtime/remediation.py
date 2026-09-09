"""Evidence-safe M4 remediation transactions and crash reconciliation."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel

from aircheck.authority import ActionExecutor, AuthorityRunContext, AuthorityStore
from aircheck.domain.actions import ActionContext, RemediationOption
from aircheck.domain.assets import Asset
from aircheck.domain.events import (
    ActionCompletedPayload,
    ActionFailedPayload,
    ActionStartedPayload,
    LedgerEvent,
    LedgerEventType,
    event_hash,
)
from aircheck.domain.state_machine import RunStateSnapshot
from aircheck.domain.types import AssetProtection, AssetRelation, AssetRole
from aircheck.media import hash_file
from aircheck.media.inspection import InspectionContext, execute_inspection
from aircheck.persistence import LocalDurableStore
from aircheck.runtime.models import RuntimeAsset, RuntimeRun
from aircheck.tools import RemediationResult, ToolStatus
from aircheck.tools.contracts import MeasureLoudnessInput, ProbeMediaInput
from aircheck.tools.paths import RuntimeAssetPath, WorkspaceResolver


class InjectedActionCrash(RuntimeError):
    """Test-only process-crash boundary; recovery sees the durable filesystem."""


class RecoveryIntegrityError(RuntimeError):
    """Durable action state cannot be reconciled without inventing success."""


@dataclass(frozen=True, slots=True)
class _OutputDescriptor:
    asset_id: str
    filename: str
    relative_path: str
    temporary_path: str
    predecessor: str | None
    relation: AssetRelation | None
    protection: AssetProtection
    role: AssetRole


class RemediationExecutor:
    def __init__(
        self,
        *,
        workspace: Path,
        store: LocalDurableStore,
        authority_store: AuthorityStore,
    ) -> None:
        self._workspace = workspace.resolve()
        self._store = store
        self._authority_store = authority_store

    def execute(
        self,
        *,
        run: RuntimeRun,
        state: RunStateSnapshot,
        authority_context: AuthorityRunContext,
        option: RemediationOption,
        payload: BaseModel,
        actor: str,
        crash_at: str | None = None,
    ) -> tuple[RuntimeRun, RemediationResult]:
        context = ActionExecutor(self._authority_store, self._store).begin(
            run=state,
            authority_context=authority_context,
            tool=option.tool,
            payload=payload,
            option=option,
            actor=actor,
        )
        descriptor = self._descriptor(context, run)
        temporary = self._inside_workspace(descriptor.temporary_path)
        final = self._inside_workspace(descriptor.relative_path)
        temporary.parent.mkdir(parents=True, exist_ok=True)
        final.parent.mkdir(parents=True, exist_ok=True)
        promoted = False
        persisted = False
        try:
            self._write_temporary(context, payload, run, temporary)
            self._verify_temporary(context, run, temporary)
            if crash_at == "after_temp":
                raise InjectedActionCrash("after_temp")
            if final.exists():
                raise FileExistsError("deterministic successor destination already exists")
            os.replace(temporary, final)
            promoted = True
            if crash_at == "after_promote":
                raise InjectedActionCrash("after_promote")
            successor = self._successor(context, run, descriptor, final)
            updated = run.model_copy(update={"assets": (*run.assets, successor)})
            self._store.save_runtime_run(updated)
            persisted = True
            self._append_completed(context, successor)
            return updated, RemediationResult(
                status=ToolStatus.OK,
                new_asset_id=successor.asset.asset_id,
            )
        except InjectedActionCrash:
            raise
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if promoted and not persisted:
                try:
                    durable = self._store.load_runtime_run(run.run_id)
                except KeyError:
                    durable = run
                persisted = any(
                    item.asset.created_by_action == context.action.action_id
                    for item in durable.assets
                )
            if persisted:
                raise
            if promoted and final.exists():
                self._quarantine(final, context.action.action_id)
            self._fail_action(context, type(exc).__name__)
            return run, RemediationResult(status=ToolStatus.ERROR, error=type(exc).__name__)

    def recover(self, *, run: RuntimeRun, state: RunStateSnapshot) -> RuntimeRun:
        if state.status.value != "REMEDIATING":
            raise ValueError("action recovery requires REMEDIATING state")
        recovered = run
        events = self._store.list_events(run.run_id)
        by_action = {
            event.payload.action_id: event
            for event in events
            if isinstance(event.payload, ActionStartedPayload)
        }
        for action_id in self._store.unfinished_actions(run.run_id):
            started = by_action[action_id]
            option = recovered.option(started.refs[0])
            context = self._recovery_context(action_id, started, option, state)
            descriptor = self._descriptor(context, recovered)
            temporary = self._inside_workspace(descriptor.temporary_path)
            final = self._inside_workspace(descriptor.relative_path)
            existing = next(
                (
                    item
                    for item in recovered.assets
                    if item.asset.created_by_action == action_id
                ),
                None,
            )
            if existing is not None:
                try:
                    if (
                        existing.relative_path != descriptor.relative_path
                        or existing.asset.asset_id != descriptor.asset_id
                        or existing.asset.filename != descriptor.filename
                        or existing.asset.predecessor != descriptor.predecessor
                        or existing.asset.relation != descriptor.relation
                        or existing.asset.protection != descriptor.protection
                        or existing.asset.role != descriptor.role
                        or not final.is_file()
                        or hash_file(final) != existing.asset.sha256
                    ):
                        raise ValueError("persisted successor binding mismatch")
                    self._verify_temporary(context, recovered, final)
                except Exception as exc:
                    if final.exists():
                        self._quarantine(final, action_id)
                    temporary.unlink(missing_ok=True)
                    self._fail_action(context, "RECOVERY_PERSISTED_ASSET_INVALID")
                    raise RecoveryIntegrityError(
                        "persisted action output failed deterministic recovery verification"
                    ) from exc
                self._append_completed(context, existing)
                temporary.unlink(missing_ok=True)
                continue
            if final.is_file():
                try:
                    self._verify_temporary(context, recovered, final)
                    successor = self._successor(context, recovered, descriptor, final)
                except Exception:
                    quarantine = final.with_name(f".{final.name}.{action_id}.quarantine")
                    os.replace(final, quarantine)
                    temporary.unlink(missing_ok=True)
                    self._fail_action(context, "RECOVERY_VERIFICATION_FAILED")
                    continue
                recovered = recovered.model_copy(
                    update={"assets": (*recovered.assets, successor)}
                )
                self._store.save_runtime_run(recovered)
                self._append_completed(context, successor)
                temporary.unlink(missing_ok=True)
                continue
            temporary.unlink(missing_ok=True)
            self._fail_action(context, "INTERRUPTED_BEFORE_PROMOTION")
        return recovered

    @staticmethod
    def _quarantine(path: Path, action_id: str) -> Path:
        quarantine = path.with_name(f".{path.name}.{action_id}.quarantine")
        os.replace(path, quarantine)
        return quarantine

    def _inside_workspace(self, relative_path: str) -> Path:
        path = (self._workspace / relative_path).resolve()
        if not path.is_relative_to(self._workspace):
            raise PermissionError("runtime output escapes the run workspace")
        return path

    @staticmethod
    def _arguments(option: RemediationOption) -> dict[str, object]:
        return {argument.name: argument.value for argument in option.tool_args}

    def _descriptor(self, context: ActionContext, run: RuntimeRun) -> _OutputDescriptor:
        action_id = context.action.action_id
        asset_id = f"asset_{action_id.removeprefix('action_')}"
        arguments = self._arguments(context.option)
        tool = context.action.tool
        if tool == "rename_delivery_copy":
            predecessor = str(arguments["asset_id"])
            source = next(item for item in run.assets if item.asset.asset_id == predecessor)
            filename = str(arguments["new_filename"])
            relation = AssetRelation.RENAMED_FROM
            role = source.asset.role
        elif tool == "convert_caption_format":
            predecessor = str(arguments["asset_id"])
            source = next(item for item in run.assets if item.asset.asset_id == predecessor)
            target_format = str(arguments["target_format"]).casefold()
            suffix = {"webvtt": ".vtt", "srt": ".srt"}.get(target_format)
            if suffix is None:
                raise ValueError("unsupported caption target format")
            filename = f"{Path(source.asset.filename).stem}{suffix}"
            relation = AssetRelation.CONVERTED_FROM
            role = AssetRole.CAPTIONS
        elif tool == "write_checksum_manifest":
            if arguments.get("format") != "sha256sums":
                raise ValueError("unsupported checksum manifest format")
            current_manifest = next(
                (
                    item
                    for item in run.current_assets()
                    if item.asset.role is AssetRole.MANIFEST
                ),
                None,
            )
            predecessor = (
                current_manifest.asset.asset_id if current_manifest is not None else None
            )
            filename = "SHA256SUMS"
            relation = AssetRelation.DERIVED_FROM if predecessor else None
            role = AssetRole.MANIFEST
        elif tool == "create_normalized_audio_derivative":
            predecessor = str(arguments["asset_id"])
            source = next(item for item in run.assets if item.asset.asset_id == predecessor)
            filename = source.asset.filename
            relation = AssetRelation.DERIVED_FROM
            role = AssetRole.PROGRAM_MASTER
        else:
            raise NotImplementedError(tool)
        protection = (
            AssetProtection.DERIVATIVE
            if tool == "create_normalized_audio_derivative"
            else AssetProtection.WORKING
        )
        root = "derivatives" if protection is AssetProtection.DERIVATIVE else "working"
        relative = f"{root}/{asset_id}/{filename}"
        return _OutputDescriptor(
            asset_id=asset_id,
            filename=filename,
            relative_path=relative,
            temporary_path=f".actions/{action_id}.tmp{Path(filename).suffix}",
            predecessor=predecessor,
            relation=relation,
            protection=protection,
            role=role,
        )

    def _write_temporary(
        self,
        context: ActionContext,
        payload: BaseModel,
        run: RuntimeRun,
        destination: Path,
    ) -> None:
        if context.action.option_id != getattr(payload, "option_id", None):
            raise PermissionError("ActionContext does not match the tool payload")
        arguments = self._arguments(context.option)
        tool = context.action.tool
        if tool in {"rename_delivery_copy", "convert_caption_format"}:
            source_id = str(arguments["asset_id"])
            source = next(item for item in run.assets if item.asset.asset_id == source_id)
            if source.asset.protection is AssetProtection.ORIGINAL:
                raise PermissionError("ORIGINAL targets are forbidden")
            source_path = self._inside_workspace(source.relative_path)
            if tool == "rename_delivery_copy":
                shutil.copyfile(source_path, destination)
            else:
                self._convert_captions(
                    source_path,
                    destination,
                    str(arguments["target_format"]),
                )
            return
        if tool == "write_checksum_manifest":
            destination.write_text(self._manifest_text(run), encoding="utf-8", newline="\n")
            return
        if tool == "create_normalized_audio_derivative":
            source_id = str(arguments["asset_id"])
            source = next(item for item in run.assets if item.asset.asset_id == source_id)
            if source.asset.protection is AssetProtection.ORIGINAL:
                raise PermissionError("ORIGINAL targets are forbidden")
            target = float(arguments["target_lufs"])
            completed = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(self._inside_workspace(source.relative_path)),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0",
                    "-c:v",
                    "copy",
                    "-af",
                    f"loudnorm=I={target}:TP=-2:LRA=7",
                    "-c:a",
                    "pcm_s16le",
                    "-ac",
                    "2",
                    "-ar",
                    "48000",
                    "-metadata",
                    "creation_time=1970-01-01T00:00:00Z",
                    "-movflags",
                    "+faststart",
                    str(destination),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if completed.returncode != 0:
                raise RuntimeError("audio derivative render failed")
            return
        raise NotImplementedError(tool)

    def _verify_temporary(
        self,
        context: ActionContext,
        run: RuntimeRun,
        path: Path,
    ) -> None:
        if not path.is_file():
            raise ValueError("remediation output is missing")
        arguments = self._arguments(context.option)
        if context.action.tool == "rename_delivery_copy":
            source_id = str(arguments["asset_id"])
            source = next(item for item in run.assets if item.asset.asset_id == source_id)
            if hash_file(path) != source.asset.sha256:
                raise ValueError("renamed successor must preserve source bytes")
            return
        if context.action.tool == "convert_caption_format":
            source_id = str(arguments["asset_id"])
            source = next(item for item in run.assets if item.asset.asset_id == source_id)
            source_path = self._inside_workspace(source.relative_path)
            if self._caption_cues(source_path) != self._caption_cues(path):
                raise ValueError("caption conversion changed cue timing or text")
            expected_suffix = {
                "webvtt": ".vtt",
                "srt": ".srt",
            }[str(arguments["target_format"]).casefold()]
            if path.suffix.casefold() != expected_suffix:
                raise ValueError("caption conversion target format mismatch")
            return
        if context.action.tool == "write_checksum_manifest":
            if path.read_text(encoding="utf-8") != self._manifest_text(run):
                raise ValueError("checksum manifest does not match current assets")
            return
        if context.action.tool == "create_normalized_audio_derivative":
            asset_id = f"asset_verify_{context.action.action_id.removeprefix('action_')}"
            relative = path.relative_to(self._workspace).as_posix()
            verification_asset = Asset(
                asset_id=asset_id,
                run_id=run.run_id,
                role=AssetRole.PROGRAM_MASTER,
                filename=path.name,
                sha256=hash_file(path),
                size_bytes=path.stat().st_size,
                protection=AssetProtection.DERIVATIVE,
            )
            inspection_context = InspectionContext(
                run_id=run.run_id,
                resolver=WorkspaceResolver(
                    self._workspace,
                    (RuntimeAssetPath(asset=verification_asset, relative_path=relative),),
                ),
                assets=(verification_asset,),
            )
            probe = execute_inspection(
                "probe_media",
                ProbeMediaInput(asset_id=asset_id),
                inspection_context,
            )
            loudness = execute_inspection(
                "measure_loudness",
                MeasureLoudnessInput(asset_id=asset_id, stream="primary_audio"),
                inspection_context,
            )
            if probe.status is not ToolStatus.OK or loudness.status is not ToolStatus.OK:
                raise ValueError("audio derivative verification failed")
            integrated = next(
                (
                    value.value
                    for value in loudness.measurements
                    if value.measurement_key == "audio.integrated_loudness"
                ),
                None,
            )
            target = float(arguments["target_lufs"])
            tolerance = float(arguments["tolerance"])
            if not isinstance(integrated, (int, float)) or abs(integrated - target) > tolerance:
                raise ValueError("audio derivative missed authorized loudness tolerance")
            return
        raise NotImplementedError(context.action.tool)

    @staticmethod
    def _caption_cues(path: Path) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
        if text.startswith("WEBVTT"):
            text = text[len("WEBVTT") :].lstrip("\n")
        cues: list[tuple[str, str, tuple[str, ...]]] = []
        for block in re.split(r"\n\s*\n", text):
            lines = [line for line in block.splitlines() if line.strip()]
            if lines and lines[0].strip().isdigit():
                lines.pop(0)
            if not lines or "-->" not in lines[0]:
                raise ValueError("caption cue is malformed")
            start, end = (part.strip().replace(",", ".") for part in lines.pop(0).split("-->", 1))
            cues.append((start, end, tuple(lines)))
        if not cues:
            raise ValueError("caption file has no cues")
        return tuple(cues)

    def _convert_captions(
        self,
        source: Path,
        destination: Path,
        target_format: str,
    ) -> None:
        cues = self._caption_cues(source)
        target = target_format.casefold()
        if target == "webvtt":
            blocks = [
                f"{start} --> {end}\n" + "\n".join(lines)
                for start, end, lines in cues
            ]
            rendered = "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"
        elif target == "srt":
            blocks = [
                f"{index}\n{start.replace('.', ',')} --> {end.replace('.', ',')}\n"
                + "\n".join(lines)
                for index, (start, end, lines) in enumerate(cues, start=1)
            ]
            rendered = "\n\n".join(blocks) + "\n"
        else:
            raise ValueError("unsupported caption target format")
        destination.write_text(rendered, encoding="utf-8", newline="\n")

    def _manifest_text(self, run: RuntimeRun) -> str:
        assets = tuple(
            item
            for item in run.current_assets()
            if item.asset.role is not AssetRole.MANIFEST
        )
        return "".join(
            f"{item.asset.sha256} *{item.asset.filename}\n"
            for item in sorted(assets, key=lambda value: value.asset.filename)
        )

    def _successor(
        self,
        context: ActionContext,
        run: RuntimeRun,
        descriptor: _OutputDescriptor,
        path: Path,
    ) -> RuntimeAsset:
        digest = hash_file(path)
        return RuntimeAsset(
            asset=Asset(
                asset_id=descriptor.asset_id,
                run_id=run.run_id,
                role=descriptor.role,
                filename=descriptor.filename,
                sha256=digest,
                size_bytes=path.stat().st_size,
                protection=descriptor.protection,
                predecessor=descriptor.predecessor,
                relation=descriptor.relation,
                created_by_action=context.action.action_id,
            ),
            relative_path=descriptor.relative_path,
        )

    @staticmethod
    def _recovery_context(
        action_id: str,
        started: LedgerEvent,
        option: RemediationOption,
        state: RunStateSnapshot,
    ) -> ActionContext:
        from aircheck.domain.actions import Action

        action = Action(
            action_id=action_id,
            run_id=state.run_id,
            cycle=state.cycle,
            tool=started.payload.tool,
            option_id=option.option_id,
            args_hash=option.args_hash,
            authorization_id=started.payload.authorization_id,
            actor=started.actor,
        )
        return ActionContext(
            action=action,
            option=option,
            authorization_id=started.payload.authorization_id,
        )

    def _append_completed(
        self,
        context: ActionContext,
        successor: RuntimeAsset,
    ) -> None:
        events = self._store.list_events(context.action.run_id)
        event = LedgerEvent(
            event_id=f"evt_{secrets.token_hex(10)}",
            run_id=context.action.run_id,
            seq=len(events) + 1,
            timestamp=datetime.now(UTC),
            type=LedgerEventType.ACTION_COMPLETED,
            actor="SYSTEM",
            summary=f"Action {context.action.action_id} completed.",
            payload=ActionCompletedPayload(
                action_id=context.action.action_id,
                new_asset_id=successor.asset.asset_id,
                sha256=successor.asset.sha256,
            ),
            refs=(context.option.option_id, successor.asset.asset_id),
            prev_hash=event_hash(events[-1]) if events else None,
        )
        self._store.append_event(event)

    def _append_failed(self, action_id: str, run_id: str, reason: str) -> None:
        events = self._store.list_events(run_id)
        event = LedgerEvent(
            event_id=f"evt_{secrets.token_hex(10)}",
            run_id=run_id,
            seq=len(events) + 1,
            timestamp=datetime.now(UTC),
            type=LedgerEventType.ACTION_FAILED,
            actor="SYSTEM",
            summary=f"Action {action_id} failed.",
            payload=ActionFailedPayload(action_id=action_id, reason=reason),
            refs=(),
            prev_hash=event_hash(events[-1]) if events else None,
        )
        self._store.append_event(event)

    def _fail_action(self, context: ActionContext, reason: str) -> None:
        if context.authorization_id is not None:
            self._authority_store.void(context.authorization_id, reason=reason)
        self._append_failed(context.action.action_id, context.action.run_id, reason)


__all__ = ("InjectedActionCrash", "RecoveryIntegrityError", "RemediationExecutor")
