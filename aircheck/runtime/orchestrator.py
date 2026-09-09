"""Canonical M4 headless execution loop and deterministic terminal control."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import Field, field_validator

from aircheck.agent.authority_hook import AuthorityHook, ToolCallDisposition
from aircheck.agent.contracts import InventoryAsset, PackageInventory, StructuredSemanticModel
from aircheck.agent.diagnosis import DiagnosisDisposition, diagnose_findings
from aircheck.agent.interpretation import interpret_requirements
from aircheck.agent.planning import construct_plan
from aircheck.authority import (
    AuthorityEngine,
    AuthorityRunContext,
    Decision,
    PendingFinding,
    approve_decision,
)
from aircheck.domain.actions import RemediationOption
from aircheck.domain.assets import Asset
from aircheck.domain.constraints import EqualsConstraint, OneOfConstraint, RangeConstraint
from aircheck.domain.events import (
    ActionCompletedPayload,
    ActionStartedPayload,
    LedgerEvent,
    LedgerEventType,
    StateTransitionPayload,
    event_hash,
)
from aircheck.domain.planning import derive_required_checks
from aircheck.domain.primitives import FrozenModel, NonEmptyStr
from aircheck.domain.source import SourceDocument
from aircheck.domain.state_machine import (
    AuthorizationBinding,
    DecisionPending,
    DecisionResolution,
    MAX_CYCLES,
    OptionBinding,
    RemediationCompleted,
    RemediationSelection,
    RunStateSnapshot,
    TransitionCause,
    transition,
)
from aircheck.domain.status import RunStatus
from aircheck.domain.terminal import (
    DecisionFact,
    FindingFact,
    Measurement,
    RequirementFact,
    TerminalInput,
    compute,
    evaluate_predicate,
)
from aircheck.domain.types import (
    ArtifactKind,
    AutomationDisposition,
    AssetProtection,
    AssetRelation,
    AssetRole,
    AuthorizationStatus,
    DecisionChoice,
    DecisionRequestStatus,
    FindingLifecycle,
    MeasurementStatus,
    PredicateOutcome,
    TerminalOutcome,
)
from aircheck.media import hash_file
from aircheck.media.inspection import InspectionContext, execute_inspection
from aircheck.persistence import LocalDurableStore
from aircheck.runtime.models import (
    RuntimeAsset,
    RuntimeEvidenceArtifact,
    RuntimeFinding,
    RuntimePredicate,
    RuntimeProvenance,
    RuntimeRequirement,
    RuntimeRun,
)
from aircheck.runtime.remediation import RecoveryIntegrityError, RemediationExecutor
from aircheck.tools import TOOL_REGISTRY, ToolStatus
from aircheck.tools.paths import RuntimeAssetPath, WorkspaceResolver


class PackageAssetInput(FrozenModel):
    filename: NonEmptyStr
    role: AssetRole

    @field_validator("filename")
    @classmethod
    def require_basename(cls, value: str) -> str:
        if (
            PureWindowsPath(value).is_absolute()
            or PurePosixPath(value).is_absolute()
            or "/" in value
            or "\\" in value
            or value in {".", ".."}
        ):
            raise ValueError("package input filename must be a basename")
        return value


def _id(prefix: str, *parts: str) -> str:
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


class HeadlessOrchestrator:
    """Provider-neutral coordinator; models recommend but never mutate or judge."""

    def __init__(
        self,
        *,
        workspace: Path,
        store_root: Path,
        run_id: str | None = None,
    ) -> None:
        self._workspace = workspace.resolve()
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._store = LocalDurableStore(store_root)
        self._run_id = run_id

    def start(
        self,
        *,
        run_id: str,
        source_root: Path,
        assets: tuple[PackageAssetInput, ...],
        document: SourceDocument,
        content_type: str,
        profile_id: str,
        profile_name: str,
        semantic_model: StructuredSemanticModel,
    ) -> RunStateSnapshot:
        if self._run_id is not None:
            raise ValueError("orchestrator is already bound to a run")
        if document.run_id != run_id:
            raise ValueError("source document belongs to another run")
        self._run_id = run_id
        state = RunStateSnapshot(run_id=run_id, status=RunStatus.CREATED)
        self._store.save_snapshot(state)
        state = self._transition(state, RunStatus.INGESTING, object(), "INGEST_REQUESTED")

        source_root = source_root.resolve()
        originals: list[RuntimeAsset] = []
        working: list[RuntimeAsset] = []
        for index, item in enumerate(assets):
            source = (source_root / item.filename).resolve()
            if not source.is_relative_to(source_root) or not source.is_file():
                raise FileNotFoundError(item.filename)
            digest = hash_file(source)
            token = _id("asset", run_id, item.role.value, str(index), item.filename).removeprefix("asset_")
            original_id = f"asset_original_{token}"
            working_id = f"asset_working_{token}"
            original_relative = f"originals/{original_id}/{item.filename}"
            working_relative = f"working/{working_id}/{item.filename}"
            for relative in (original_relative, working_relative):
                destination = self._workspace / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    raise FileExistsError(relative)
                shutil.copyfile(source, destination)
            original = RuntimeAsset(
                asset=Asset(
                    asset_id=original_id,
                    run_id=run_id,
                    role=item.role,
                    filename=item.filename,
                    sha256=digest,
                    size_bytes=source.stat().st_size,
                    protection=AssetProtection.ORIGINAL,
                ),
                relative_path=original_relative,
            )
            originals.append(original)
            working.append(
                RuntimeAsset(
                    asset=Asset(
                        asset_id=working_id,
                        run_id=run_id,
                        role=item.role,
                        filename=item.filename,
                        sha256=digest,
                        size_bytes=source.stat().st_size,
                        protection=AssetProtection.WORKING,
                        predecessor=original_id,
                        relation=AssetRelation.COPIED_FROM,
                    ),
                    relative_path=working_relative,
                )
            )
        run = RuntimeRun(
            run_id=run_id,
            profile_id=profile_id,
            content_type=content_type,
            source_documents=(document,),
            assets=(*originals, *working),
            original_hashes=tuple((item.asset.asset_id, item.asset.sha256) for item in originals),
        )
        self._store.save_runtime_run(run)
        state = self._transition(state, RunStatus.INGESTED, object(), "INGEST_COMPLETED")
        state = self._transition(state, RunStatus.INTERPRETING, object(), "INTERPRETATION_STARTED")

        current = run.current_assets()
        inventory = PackageInventory(
            run_id=run_id,
            content_type=content_type,
            destination_profile=profile_name,
            assets=tuple(
                InventoryAsset(
                    asset_id=item.asset.asset_id,
                    role=item.asset.role,
                    filename=item.asset.filename,
                )
                for item in current
            ),
        )
        interpreted = interpret_requirements(semantic_model, document, inventory)
        requirements = tuple(
            RuntimeRequirement(
                requirement_id=item.requirement_id,
                measurement_key=(item.verification.measurement_key if item.verification else None),
                verification_tool=(item.verification.tool if item.verification else None),
                stream=item.scope.stream,
                asset_role=item.scope.asset_role.value,
                constraint=item.constraint,
                severity=item.severity,
                normalization_status=item.normalization_status,
                applicability_status=item.applicability_status,
                disposition=item.automation_disposition,
                rendered_text=item.rendered_text,
                provenance=RuntimeProvenance.from_source_span(item.span),
                status_reasons=item.status_reasons,
            )
            for item in interpreted.requirements
        )
        checks = derive_required_checks(
            interpreted.requirements,
            tuple(item.asset for item in current),
        )
        run = run.model_copy(update={"requirements": requirements})
        self._store.save_runtime_run(run)
        state = self._transition(
            state,
            RunStatus.REQUIREMENTS_ADMITTED,
            object(),
            "REQUIREMENTS_ADMITTED",
        )
        planning = construct_plan(
            semantic_model,
            run_id=run_id,
            cycle=0,
            required_checks=checks,
            assets=tuple(item.asset for item in current),
        )
        check_order = tuple(
            item.requirement_id
            for item in planning.plan.items
            if item.requirement_id is not None
        )
        run = run.model_copy(update={"check_order": check_order})
        self._store.save_runtime_run(run)
        state = self._transition(state, RunStatus.QC_PLANNED, object(), "QC_PLAN_VALIDATED")
        state = self._transition(state, RunStatus.INSPECTING, object(), "INSPECTION_STARTED")
        return self._inspect(state)

    def load_state(self) -> RunStateSnapshot:
        return self._store.load_snapshot(self._required_run_id())

    def load_run(self) -> RuntimeRun:
        return self._store.load_runtime_run(self._required_run_id())

    def load_pending_decision(self):
        return self._store.load_pending_decision(self._required_run_id())

    def events(self) -> tuple[LedgerEvent, ...]:
        return self._store.list_events(self._required_run_id())

    def evidence_paths(self) -> set[str]:
        root = self._workspace / "evidence"
        return {path.name for path in root.iterdir() if path.is_file()} if root.exists() else set()

    def advance(self, semantic_model: StructuredSemanticModel) -> RunStateSnapshot:
        state = self.load_state()
        if state.status in {
            RunStatus.DELIVERY_READY,
            RunStatus.BLOCKED,
            RunStatus.FAILED,
        }:
            run = self._reconcile_remediation_counts(self.load_run())
            self._store.save_runtime_run(run)
            if not self._evidence_complete(run):
                self._render_evidence(run, state)
            return state
        if state.status is RunStatus.REMEDIATING:
            try:
                run = RemediationExecutor(
                    workspace=self._workspace,
                    store=self._store,
                    authority_store=self._store,
                ).recover(run=self.load_run(), state=state)
            except RecoveryIntegrityError:
                run = self.load_run()
                state = self._transition(
                    state,
                    RunStatus.FAILED,
                    object(),
                    "RECOVERY_INTEGRITY_FAILED",
                )
                self._render_evidence(run, state)
                return state
            run, refreshed = self._refresh_generated_manifest(run, state)
            del refreshed
            run = self._reconcile_remediation_counts(run)
            self._store.save_runtime_run(run)
            state = self._transition(
                state,
                RunStatus.INSPECTING,
                RemediationCompleted(run_id=run.run_id, next_cycle=state.cycle + 1),
                "INTERRUPTED_REMEDIATION_RECOVERED",
            )
            return self._inspect(state)
        if state.status is not RunStatus.FINDINGS_READY:
            return state
        run = self._reconcile_remediation_counts(self.load_run())
        self._store.save_runtime_run(run)
        verdict = self._terminal(run, state)
        if verdict is not None:
            state = self._transition(
                state,
                RunStatus(verdict.outcome.value),
                verdict,
                "TERMINAL_EVALUATED",
            )
            self._render_evidence(run, state)
            return state

        if state.cycle >= MAX_CYCLES:
            return self._block_exhausted(run, state)
        diagnosis = diagnose_findings(semantic_model, run)
        by_option = {
            option.option_id: option
            for finding in run.findings
            for option in finding.options
        }
        tier_one = [
            by_option[item.option_id]
            for item in diagnosis.items
            if item.disposition is DiagnosisDisposition.APPLIED and item.option_id is not None
        ]
        if tier_one:
            priority = {
                "rename_delivery_copy": 0,
                "convert_caption_format": 1,
                "write_checksum_manifest": 2,
            }
            tier_one.sort(key=lambda option: (priority.get(option.tool, 99), option.option_id))
            return self._execute_tier_one(run, state, tuple(tier_one))

        escalated = next(
            (
                by_option[item.option_id]
                for item in diagnosis.items
                if item.disposition is DiagnosisDisposition.ESCALATE
                and item.option_id is not None
            ),
            None,
        )
        if escalated is not None:
            payload = self._payload(escalated)
            result = AuthorityHook(AuthorityEngine(self._store)).evaluate_agent_call(
                tool=escalated.tool,
                payload=payload,
                run=self._authority_context(run, state),
                option=escalated,
                now=datetime.now(UTC),
            )
            if result.disposition is not ToolCallDisposition.INTERRUPT or result.decision_request is None:
                raise PermissionError(result.reason)
            request = result.decision_request
            self._store.save_decision_request(request)
            run = run.model_copy(update={"decision_requests": (*run.decision_requests, request)})
            self._store.save_runtime_run(run)
            binding = self._binding(escalated)
            return self._transition(
                state,
                RunStatus.AWAITING_HUMAN_DECISION,
                DecisionPending(run_id=run.run_id, decision_id=request.decision_id, option=binding),
                "HUMAN_DECISION_REQUIRED",
            )

        unresolved = tuple(
            finding.model_copy(update={"status": FindingLifecycle.UNRESOLVED, "options": ()})
            if finding.status is FindingLifecycle.OPEN
            else finding
            for finding in run.findings
        )
        run = run.model_copy(update={"findings": unresolved})
        self._store.save_runtime_run(run)
        verdict = self._terminal(run, state)
        if verdict is None:
            raise RuntimeError("deterministic terminal evaluation remained non-terminal")
        state = self._transition(
            state,
            RunStatus(verdict.outcome.value),
            verdict,
            "NO_ADMISSIBLE_ACTION",
        )
        self._render_evidence(run, state)
        return state

    def _block_exhausted(
        self,
        run: RuntimeRun,
        state: RunStateSnapshot,
    ) -> RunStateSnapshot:
        findings = tuple(
            item.model_copy(update={"status": FindingLifecycle.UNRESOLVED, "options": ()})
            if item.status is FindingLifecycle.OPEN
            else item
            for item in run.findings
        )
        run = run.model_copy(update={"findings": findings})
        self._store.save_runtime_run(run)
        verdict = self._terminal(run, state)
        if verdict is None or verdict.outcome is not TerminalOutcome.BLOCKED:
            raise RuntimeError("cycle exhaustion must deterministically block")
        state = self._transition(
            state,
            RunStatus.BLOCKED,
            verdict,
            "REMEDIATION_CYCLES_EXHAUSTED",
        )
        self._render_evidence(run, state)
        return state

    def resolve_decision(self, *, approved: bool, actor: str) -> RunStateSnapshot:
        state = self.load_state()
        if state.status is not RunStatus.AWAITING_HUMAN_DECISION:
            raise ValueError("run is not awaiting a human decision")
        request = self._store.load_pending_decision(state.run_id)
        run = self.load_run()
        option = run.option(request.option_id)
        if not actor.startswith("human:"):
            raise ValueError("decision actor must identify a human")
        if not approved:
            decided_at = datetime.now(UTC)
            updated_request = request.model_copy(update={"status": DecisionRequestStatus.DENIED})
            decision = Decision(
                decision_id=request.decision_id,
                actor=actor,
                choice=DecisionChoice.DENIED,
                decided_at=decided_at,
            )
            findings = tuple(
                finding.model_copy(update={"status": FindingLifecycle.UNRESOLVED, "options": ()})
                if finding.finding_id == request.finding_id
                else finding
                for finding in run.findings
            )
            run = run.model_copy(
                update={
                    "decision_requests": self._replace_request(run, updated_request),
                    "decisions": (*run.decisions, decision),
                    "findings": findings,
                }
            )
            self._store.update_decision_request(updated_request)
            self._store.save_runtime_run(run)
            state = self._transition(
                state,
                RunStatus.BLOCKED,
                DecisionResolution(
                    run_id=run.run_id,
                    decision_id=request.decision_id,
                    choice=DecisionChoice.DENIED,
                    admissible_options_remain=False,
                ),
                "HUMAN_DECISION_DENIED",
            )
            state = state.model_copy(update={"admissible_options": ()})
            self._store.save_snapshot(state)
            self._render_evidence(run, state)
            return state

        approval = approve_decision(request, option, actor=actor, store=self._store)
        assert approval.authorization is not None
        self._store.update_decision_request(approval.request)
        run = run.model_copy(
            update={
                "decision_requests": self._replace_request(run, approval.request),
                "decisions": (*run.decisions, approval.decision),
            }
        )
        self._store.save_runtime_run(run)
        authorization = approval.authorization
        state = self._transition(
            state,
            RunStatus.REMEDIATING,
            AuthorizationBinding(
                authorization_id=authorization.authorization_id,
                run_id=authorization.run_id,
                decision_id=authorization.decision_id,
                option_id=authorization.option_id,
                args_hash=authorization.args_hash,
                status=AuthorizationStatus.ISSUED,
            ),
            "HUMAN_DECISION_APPROVED",
        )
        executor = RemediationExecutor(
            workspace=self._workspace,
            store=self._store,
            authority_store=self._store,
        )
        payload = self._payload(option, authorization_id=authorization.authorization_id)
        run, result = executor.execute(
            run=run,
            state=state,
            authority_context=self._authority_context(run, state, policy_state=RunStatus.FINDINGS_READY),
            option=option,
            payload=payload,
            actor="SYSTEM",
        )
        if result.status is ToolStatus.OK:
            run, refreshed = self._refresh_generated_manifest(run, state)
            del refreshed
        run = self._reconcile_remediation_counts(run)
        self._store.save_runtime_run(run)
        state = self._transition(
            state,
            RunStatus.INSPECTING,
            RemediationCompleted(run_id=run.run_id, next_cycle=state.cycle + 1),
            "REMEDIATION_COMPLETED",
        )
        return self._inspect(state)

    def _execute_tier_one(
        self,
        run: RuntimeRun,
        state: RunStateSnapshot,
        options: tuple[RemediationOption, ...],
    ) -> RunStateSnapshot:
        authority_context = self._authority_context(run, state)
        payloads = tuple(self._payload(option) for option in options)
        hook = AuthorityHook(AuthorityEngine(self._store))
        for option, payload in zip(options, payloads):
            result = hook.evaluate_agent_call(
                tool=option.tool,
                payload=payload,
                run=authority_context,
                option=option,
                now=datetime.now(UTC),
            )
            if result.disposition is not ToolCallDisposition.ALLOW:
                raise PermissionError(result.reason)
        first = options[0]
        state = self._transition(
            state,
            RunStatus.REMEDIATING,
            RemediationSelection(**self._binding(first).model_dump()),
            "TIER1_REMEDIATION_SELECTED",
        )
        executor = RemediationExecutor(
            workspace=self._workspace,
            store=self._store,
            authority_store=self._store,
        )
        for option, payload in zip(options, payloads):
            run, result = executor.execute(
                run=run,
                state=state,
                authority_context=authority_context,
                option=option,
                payload=payload,
                actor="SYSTEM",
            )
            del result
        run, refreshed = self._refresh_generated_manifest(run, state)
        del refreshed
        run = self._reconcile_remediation_counts(run)
        self._store.save_runtime_run(run)
        state = self._transition(
            state,
            RunStatus.INSPECTING,
            RemediationCompleted(run_id=run.run_id, next_cycle=state.cycle + 1),
            "REMEDIATION_COMPLETED",
        )
        return self._inspect(state)

    def _inspect(self, state: RunStateSnapshot) -> RunStateSnapshot:
        run = self.load_run()
        current = run.current_assets()
        context = InspectionContext(
            run_id=run.run_id,
            resolver=WorkspaceResolver(
                self._workspace,
                tuple(
                    RuntimeAssetPath(asset=item.asset, relative_path=item.relative_path)
                    for item in current
                ),
            ),
            assets=tuple(item.asset for item in current),
        )
        by_requirement = {item.requirement_id: item for item in run.requirements}
        measurements: list[Measurement] = []
        predicates: list[RuntimePredicate] = []
        findings = [
            item.model_copy(update={"status": FindingLifecycle.RESOLVED})
            if item.status is FindingLifecycle.OPEN
            else item
            for item in run.findings
        ]
        cache: dict[str, object] = {}
        ordered_ids = run.check_order or tuple(item.requirement_id for item in run.requirements)
        for requirement_id in ordered_ids:
            requirement = by_requirement[requirement_id]
            if requirement.verification_tool is None or requirement.constraint is None:
                continue
            scoped = tuple(
                item for item in current if item.asset.role.value == requirement.asset_role
            )
            target = scoped[0] if scoped else current[0]
            specification = TOOL_REGISTRY[requirement.verification_tool]
            values: dict[str, object] = {}
            fields = specification.input_model.model_fields
            if "run_id" in fields:
                values["run_id"] = run.run_id
            if "asset_id" in fields:
                values["asset_id"] = target.asset.asset_id
            if "stream" in fields:
                values["stream"] = requirement.stream
            payload = specification.input_model(**values)
            cache_key = f"{requirement.verification_tool}:{payload.model_dump_json()}"
            result = cache.get(cache_key)
            if result is None:
                authority = AuthorityEngine(self._store).evaluate(
                    requirement.verification_tool,
                    payload,
                    self._authority_context(run, state),
                )
                if authority.outcome.value != "ALLOW":
                    raise PermissionError(authority.reason)
                result = execute_inspection(requirement.verification_tool, payload, context)
                cache[cache_key] = result
            matching = next(
                (
                    item
                    for item in result.measurements
                    if item.measurement_key == requirement.measurement_key
                    and (item.asset_id is None or item.asset_id == target.asset.asset_id)
                ),
                None,
            )
            measurement_id = _id("measurement", run.run_id, requirement_id, str(state.cycle))
            if matching is None:
                measurement = Measurement(
                    measurement_id=measurement_id,
                    run_id=run.run_id,
                    cycle=state.cycle,
                    requirement_id=requirement_id,
                    measurement_key=requirement.measurement_key,
                    unit=requirement.constraint.unit,
                    status=MeasurementStatus.ERROR,
                    error=result.error or "MEASUREMENT_ABSENT",
                )
                observed = measurement.error or "not evaluated"
            else:
                measurement = Measurement(
                    measurement_id=measurement_id,
                    run_id=run.run_id,
                    cycle=state.cycle,
                    requirement_id=requirement_id,
                    measurement_key=matching.measurement_key,
                    value=matching.value,
                    unit=matching.unit,
                    status=matching.status,
                    error=matching.error,
                )
                observed = str(matching.value)
            predicate = evaluate_predicate(measurement, requirement.constraint)
            measurements.append(measurement)
            predicates.append(
                RuntimePredicate(
                    requirement_id=requirement_id,
                    measurement_id=measurement_id,
                    cycle=state.cycle,
                    outcome=predicate.result,
                )
            )
            if predicate.result is not PredicateOutcome.PASS:
                finding_id = _id("finding", run.run_id, requirement_id, str(state.cycle))
                options = self._options(
                    run=run,
                    requirement=requirement,
                    finding_id=finding_id,
                    target=target,
                    observed=observed,
                )
                findings.append(
                    RuntimeFinding(
                        finding_id=finding_id,
                        requirement_id=requirement_id,
                        asset_id=target.asset.asset_id,
                        cycle_opened=state.cycle,
                        status=FindingLifecycle.OPEN,
                        options=options,
                        observed_rendered=observed,
                        expected_rendered=requirement.rendered_text,
                    )
                )
        run = run.model_copy(
            update={
                "measurements": (*run.measurements, *measurements),
                "predicates": (*run.predicates, *predicates),
                "findings": tuple(findings),
            }
        )
        self._store.save_runtime_run(run)
        state = self._transition(
            state,
            RunStatus.FINDINGS_READY,
            object(),
            "INSPECTION_COMPLETED",
        )
        bindings = tuple(
            self._binding(option)
            for finding in run.findings
            if finding.status is FindingLifecycle.OPEN
            for option in finding.options
        )
        state = state.model_copy(update={"admissible_options": bindings})
        self._store.save_snapshot(state)
        return state

    def _options(
        self,
        *,
        run: RuntimeRun,
        requirement: RuntimeRequirement,
        finding_id: str,
        target: RuntimeAsset,
        observed: str,
    ) -> tuple[RemediationOption, ...]:
        constraint = requirement.constraint
        tool: str | None = None
        arguments: dict[str, object] = {}
        description = ""
        tier = 1
        derivative = False
        key = requirement.measurement_key
        if requirement.disposition not in {
            AutomationDisposition.AUTO_REMEDIATE,
            AutomationDisposition.HUMAN_APPROVAL_REQUIRED,
        }:
            return ()
        if key == "package.filename[role]" and isinstance(constraint, EqualsConstraint):
            if requirement.disposition is not AutomationDisposition.AUTO_REMEDIATE:
                return ()
            tool = "rename_delivery_copy"
            arguments = {"asset_id": target.asset.asset_id, "new_filename": str(constraint.value)}
            description = "Create a renamed working successor with the required delivery filename."
        elif key == "package.manifest_present":
            if requirement.disposition is not AutomationDisposition.AUTO_REMEDIATE:
                return ()
            tool = "write_checksum_manifest"
            arguments = {"run_id": run.run_id, "format": "sha256sums"}
            description = "Write a checksum manifest for the current package leaves."
        elif key == "captions.format":
            if requirement.disposition is not AutomationDisposition.AUTO_REMEDIATE:
                return ()
            target_format: str | None = None
            if isinstance(constraint, EqualsConstraint):
                target_format = str(constraint.value)
            elif isinstance(constraint, OneOfConstraint) and constraint.values:
                target_format = str(constraint.values[0])
            if target_format is not None:
                tool = "convert_caption_format"
                arguments = {"asset_id": target.asset.asset_id, "target_format": target_format}
                description = "Create a deterministically equivalent caption-format successor."
        elif key == "audio.integrated_loudness" and isinstance(constraint, RangeConstraint):
            if requirement.disposition is not AutomationDisposition.HUMAN_APPROVAL_REQUIRED:
                return ()
            tool = "create_normalized_audio_derivative"
            midpoint = (float(constraint.lower) + float(constraint.upper)) / 2
            tolerance = (float(constraint.upper) - float(constraint.lower)) / 2
            arguments = {
                "asset_id": target.asset.asset_id,
                "target_lufs": midpoint,
                "tolerance": tolerance,
                "authorization_id": "auth_placeholder",
            }
            description = "Create one authorized normalized-audio delivery derivative."
            tier = 2
            derivative = True
        if tool is None or self._failure_count(run, requirement.requirement_id) >= 2:
            return ()
        option_id = _id("option", run.run_id, finding_id, tool, observed)
        arguments["option_id"] = option_id
        payload = TOOL_REGISTRY[tool].input_model(**arguments)
        return (
            RemediationOption.from_call(
                option_id=option_id,
                finding_id=finding_id,
                tool=tool,
                payload=payload,
                tier=tier,
                description=description,
                produces_derivative=derivative,
            ),
        )

    def _refresh_generated_manifest(
        self,
        run: RuntimeRun,
        state: RunStateSnapshot,
    ) -> tuple[RuntimeRun, int]:
        """Regenerate only an intact runtime manifest invalidated by a later action."""

        manifest = next(
            (
                item
                for item in run.current_assets()
                if item.asset.role is AssetRole.MANIFEST
                and item.asset.created_by_action is not None
            ),
            None,
        )
        if manifest is None:
            return run, 0
        manifest_path = self._workspace / manifest.relative_path
        if not manifest_path.is_file() or hash_file(manifest_path) != manifest.asset.sha256:
            return run, 0

        events = self.events()
        starts = {
            event.payload.action_id: event.payload.tool
            for event in events
            if isinstance(event.payload, ActionStartedPayload)
        }
        completions = {
            event.payload.action_id: event.seq
            for event in events
            if isinstance(event.payload, ActionCompletedPayload)
        }
        manifest_seq = completions.get(manifest.asset.created_by_action)
        changed_seq = max(
            (
                seq
                for action_id, seq in completions.items()
                if starts.get(action_id) in {
                    "rename_delivery_copy",
                    "convert_caption_format",
                    "create_normalized_audio_derivative",
                }
            ),
            default=-1,
        )
        if manifest_seq is None or changed_seq <= manifest_seq:
            return run, 0

        requirement = next(
            (
                item
                for item in run.requirements
                if item.measurement_key == "package.checksums_match"
            ),
            None,
        )
        if requirement is None:
            return run, 0
        finding_id = _id(
            "finding",
            run.run_id,
            requirement.requirement_id,
            str(state.cycle),
            "runtime-manifest-invalidation",
        )
        option_id = _id("option", run.run_id, finding_id, "write_checksum_manifest")
        payload = TOOL_REGISTRY["write_checksum_manifest"].input_model(
            run_id=run.run_id,
            option_id=option_id,
            format="sha256sums",
        )
        option = RemediationOption.from_call(
            option_id=option_id,
            finding_id=finding_id,
            tool="write_checksum_manifest",
            payload=payload,
            tier=1,
            description="Regenerate the runtime manifest after a committed package mutation.",
            produces_derivative=False,
        )
        finding = RuntimeFinding(
            finding_id=finding_id,
            requirement_id=requirement.requirement_id,
            asset_id=manifest.asset.asset_id,
            cycle_opened=state.cycle,
            status=FindingLifecycle.OPEN,
            options=(option,),
            observed_rendered="runtime manifest invalidated by a later committed action",
            expected_rendered=requirement.rendered_text,
        )
        run = run.model_copy(update={"findings": (*run.findings, finding)})
        self._store.save_runtime_run(run)
        authority_context = self._authority_context(
            run,
            state,
            policy_state=RunStatus.FINDINGS_READY,
        )
        allowed = AuthorityHook(AuthorityEngine(self._store)).evaluate_agent_call(
            tool=option.tool,
            payload=payload,
            run=authority_context,
            option=option,
            now=datetime.now(UTC),
        )
        if allowed.disposition is not ToolCallDisposition.ALLOW:
            raise PermissionError(allowed.reason)
        updated, result = RemediationExecutor(
            workspace=self._workspace,
            store=self._store,
            authority_store=self._store,
        ).execute(
            run=run,
            state=state,
            authority_context=authority_context,
            option=option,
            payload=payload,
            actor="SYSTEM",
        )
        if result.status is not ToolStatus.OK:
            failed = tuple(
                item.model_copy(update={"status": FindingLifecycle.UNRESOLVED, "options": ()})
                if item.finding_id == finding_id
                else item
                for item in updated.findings
            )
            updated = updated.model_copy(update={"findings": failed})
            self._store.save_runtime_run(updated)
            return updated, 0
        successor = next(
            item for item in updated.assets if item.asset.asset_id == result.new_asset_id
        )
        resolved = tuple(
            item.model_copy(
                update={
                    "status": FindingLifecycle.RESOLVED,
                    "options": (),
                    "resolved_by_action": successor.asset.created_by_action,
                }
            )
            if item.finding_id == finding_id
            else item
            for item in updated.findings
        )
        updated = updated.model_copy(update={"findings": resolved})
        self._store.save_runtime_run(updated)
        return updated, 1

    @staticmethod
    def _payload(option: RemediationOption, *, authorization_id: str | None = None):
        values = {argument.name: argument.value for argument in option.tool_args}
        if "authorization_id" in TOOL_REGISTRY[option.tool].input_model.model_fields:
            values["authorization_id"] = authorization_id or "auth_placeholder"
        return TOOL_REGISTRY[option.tool].input_model(**values)

    def _binding(self, option: RemediationOption) -> OptionBinding:
        return OptionBinding(
            run_id=self._required_run_id(),
            finding_id=option.finding_id,
            option_id=option.option_id,
            args_hash=option.args_hash,
            tier=option.tier,
        )

    def _authority_context(
        self,
        run: RuntimeRun,
        state: RunStateSnapshot,
        *,
        policy_state: RunStatus | None = None,
    ) -> AuthorityRunContext:
        return AuthorityRunContext(
            run_id=run.run_id,
            status=policy_state or state.status,
            assets=tuple(item.asset for item in run.current_assets()),
            findings=tuple(
                PendingFinding(finding_id=item.finding_id, status=item.status)
                for item in run.findings
            ),
            options=tuple(option for finding in run.findings for option in finding.options),
            pending_decision=(
                next(
                    (
                        item
                        for item in run.decision_requests
                        if item.status is DecisionRequestStatus.PENDING
                    ),
                    None,
                )
            ),
        )

    def _terminal(self, run: RuntimeRun, state: RunStateSnapshot):
        predicate_results = tuple(
            evaluate_predicate(measurement, requirement.constraint)
            for measurement in run.measurements
            for requirement in run.requirements
            if requirement.requirement_id == measurement.requirement_id
            and requirement.constraint is not None
        )
        return compute(
            TerminalInput(
                run_id=run.run_id,
                cycle=state.cycle,
                requirements=tuple(
                    RequirementFact(
                        requirement_id=item.requirement_id,
                        severity=item.severity,
                        normalization_status=item.normalization_status,
                        applicability_status=item.applicability_status,
                    )
                    for item in run.requirements
                ),
                predicates=predicate_results,
                findings=tuple(
                    FindingFact(
                        requirement_id=item.requirement_id,
                        status=item.status,
                        has_admissible_option=bool(item.options),
                    )
                    for item in run.findings
                ),
                decisions=tuple(DecisionFact(status=item.status) for item in run.decision_requests),
                originals_integrity_verified=self._originals_intact(run),
                autonomous_remediations=run.autonomous_remediations,
                authorized_remediations=run.authorized_remediations,
            )
        )

    def _originals_intact(self, run: RuntimeRun) -> bool:
        expected = dict(run.original_hashes)
        return all(
            (self._workspace / item.relative_path).is_file()
            and hash_file(self._workspace / item.relative_path) == expected[item.asset.asset_id]
            for item in run.assets
            if item.asset.protection is AssetProtection.ORIGINAL
        )

    def _failure_count(self, run: RuntimeRun, requirement_id: str) -> int:
        option_requirements = {
            option.option_id: finding.requirement_id
            for finding in run.findings
            for option in finding.options
        }
        started_options = {
            event.payload.action_id: event.refs[0]
            for event in self.events()
            if isinstance(event.payload, ActionStartedPayload) and event.refs
        }
        from aircheck.domain.events import ActionFailedPayload

        return sum(
            option_requirements.get(started_options.get(event.payload.action_id, ""))
            == requirement_id
            for event in self.events()
            if isinstance(event.payload, ActionFailedPayload)
        )

    def _reconcile_remediation_counts(self, run: RuntimeRun) -> RuntimeRun:
        events = self.events()
        starts = {
            event.payload.action_id: event.payload
            for event in events
            if isinstance(event.payload, ActionStartedPayload)
        }
        completed = {
            event.payload.action_id
            for event in events
            if isinstance(event.payload, ActionCompletedPayload)
        }
        authorized = sum(
            starts[action_id].authorization_id is not None
            for action_id in completed
            if action_id in starts
        )
        autonomous = sum(
            starts[action_id].authorization_id is None
            for action_id in completed
            if action_id in starts
        )
        if (
            run.autonomous_remediations,
            run.authorized_remediations,
        ) == (autonomous, authorized):
            return run
        return run.model_copy(
            update={
                "autonomous_remediations": autonomous,
                "authorized_remediations": authorized,
            }
        )

    def _transition(
        self,
        state: RunStateSnapshot,
        target: RunStatus,
        guard: object,
        event_type: str,
    ) -> RunStateSnapshot:
        updated = transition(
            state,
            target,
            cause=TransitionCause(run_id=state.run_id, actor="SYSTEM", event_type=event_type),
            guard_payload=guard,
        )
        events = self._store.list_events(state.run_id)
        event = LedgerEvent(
            event_id=f"evt_{secrets.token_hex(10)}",
            run_id=state.run_id,
            seq=len(events) + 1,
            timestamp=datetime.now(UTC),
            type=LedgerEventType.STATE_TRANSITION,
            actor="SYSTEM",
            summary=f"Run transitioned from {state.status.value} to {target.value}.",
            payload=StateTransitionPayload(from_state=state.status, to_state=target),
            refs=(event_type,),
            prev_hash=event_hash(events[-1]) if events else None,
        )
        self._store.commit_transition(updated, event)
        return updated

    def _render_evidence(self, run: RuntimeRun, state: RunStateSnapshot) -> None:
        root = self._workspace / "evidence"
        root.mkdir(parents=True, exist_ok=True)
        events = []
        for event in self.events():
            value = event.model_dump(mode="json")
            if isinstance(event.payload, ActionStartedPayload):
                value["payload"]["authorization_id"] = (
                    "REDACTED_PRESENT" if event.payload.authorization_id else None
                )
            events.append(value)
        payloads = {
            "ledger.json": {"run_id": run.run_id, "events": events},
            "lineage.json": {
                "run_id": run.run_id,
                "assets": [item.asset.model_dump(mode="json") for item in run.assets],
                "originals_integrity_verified": self._originals_intact(run),
            },
            "qc-report.json": {
                "run_id": run.run_id,
                "source_documents": [
                    {
                        "doc_id": item.doc_id,
                        "kind": item.kind.value,
                        "title": item.title,
                        "raw_sha256": item.raw_sha256,
                        "normalized_sha256": item.normalized_sha256,
                        "ingested_at": item.ingested_at.isoformat(),
                    }
                    for item in run.source_documents
                ],
                "requirements": [item.model_dump(mode="json") for item in run.requirements],
                "measurements": [item.model_dump(mode="json") for item in run.measurements],
                "predicates": [item.model_dump(mode="json") for item in run.predicates],
                "findings": [item.model_dump(mode="json") for item in run.findings],
                "decision_requests": [
                    item.model_dump(mode="json") for item in run.decision_requests
                ],
                "decisions": [item.model_dump(mode="json") for item in run.decisions],
                "authorizations": [
                    {
                        "authorization": "REDACTED_PRESENT",
                        "decision_id": item.decision_id,
                        "option_id": item.option_id,
                        "args_hash": item.args_hash,
                        "status": item.status.value,
                        "issued_at": item.issued_at.isoformat(),
                        "consumed_at": (
                            item.consumed_at.isoformat() if item.consumed_at else None
                        ),
                        "voided_at": (
                            item.voided_at.isoformat() if item.voided_at else None
                        ),
                        "consumed_by_action_id": item.consumed_by_action_id,
                        "void_reason": item.void_reason,
                    }
                    for item in self._store.list_authorizations(run.run_id)
                ],
            },
            "terminal.json": {
                "run_id": run.run_id,
                "status": state.status.value,
                "cycle": state.cycle,
                "verdict": (
                    state.terminal_verdict.model_dump(mode="json")
                    if state.terminal_verdict is not None
                    else None
                ),
            },
        }
        kinds = {
            "ledger.json": ArtifactKind.LEDGER_EXPORT,
            "lineage.json": ArtifactKind.LINEAGE,
            "qc-report.json": ArtifactKind.QC_REPORT,
            "terminal.json": ArtifactKind.TOOL_OUTPUT,
        }
        evidence: list[RuntimeEvidenceArtifact] = []
        for filename, payload in payloads.items():
            path = root / filename
            self._atomic_artifact_write(
                path,
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
            )
            evidence.append(
                RuntimeEvidenceArtifact(
                    evidence_id=_id("evidence", run.run_id, filename),
                    kind=kinds[filename],
                    sha256=hash_file(path),
                    relative_path=f"evidence/{filename}",
                )
            )
        self._store.save_runtime_run(run.model_copy(update={"evidence": tuple(evidence)}))

    def _evidence_complete(self, run: RuntimeRun) -> bool:
        expected = {
            "evidence/ledger.json",
            "evidence/lineage.json",
            "evidence/qc-report.json",
            "evidence/terminal.json",
        }
        recorded = {item.relative_path: item for item in run.evidence}
        if set(recorded) != expected:
            return False
        return all(
            (self._workspace / relative).is_file()
            and hash_file(self._workspace / relative) == recorded[relative].sha256
            for relative in expected
        )

    @staticmethod
    def _atomic_artifact_write(path: Path, text: str) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _replace_request(run: RuntimeRun, replacement):
        return tuple(
            replacement if item.decision_id == replacement.decision_id else item
            for item in run.decision_requests
        )

    def _required_run_id(self) -> str:
        if self._run_id is None:
            raise ValueError("orchestrator is not bound to a run")
        return self._run_id


__all__ = ("HeadlessOrchestrator", "PackageAssetInput")
