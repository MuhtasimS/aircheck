"""AIRCheck application service: read projections and command delegation.

This module is the M5 application boundary. It is a façade over the frozen M4
deterministic runtime: it constructs product-safe read projections from durable
runtime truth and delegates every command (create/start/decide) to the
``HeadlessOrchestrator``. It owns NO domain truth — it never computes a terminal
verdict, evaluates a predicate, grants authority, or mutates run state itself.
The runtime remains authoritative for run state, media facts, predicate results,
authority, side effects, lineage, evidence, and the terminal verdict.

Projection formatting and command delegation are the only responsibilities here.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from aircheck.domain.constraints import (
    EqualsConstraint,
    MaxConstraint,
    MinConstraint,
    RangeConstraint,
    render_constraint,
)
from aircheck.domain.events import (
    ActionCompletedPayload,
    ActionFailedPayload,
    ActionStartedPayload,
    LedgerEvent,
)
from aircheck.domain.source import ingest_source_document, segment_source_document
from aircheck.domain.status import RunStatus, TERMINAL_STATUSES
from aircheck.domain.types import (
    AssetRole,
    DecisionRequestStatus,
    FindingLifecycle,
    PredicateOutcome,
    SourceKind,
    TerminalOutcome,
)
from aircheck.media import hash_file
from aircheck.persistence.s3_lock import ConflictError
from aircheck.persistence.store import LocalDurableStore
from aircheck.runtime.models import RuntimeRequirement, RuntimeRun
from aircheck.runtime.orchestrator import HeadlessOrchestrator, PackageAssetInput
from apps.api.demo_semantics import DemoSemanticModel
from apps.api.models import (
    AgentCoreInvocationRecord,
    AgentCoreProvenance,
    AssetView,
    CreateRunRequest,
    CreateRunResponse,
    DecideResponse,
    DeliveriesCounts,
    DeliveriesResponse,
    DeliverySummary,
    EvidenceArtifactView,
    EvidenceMetrics,
    EvidenceResponse,
    EvidenceVerificationRow,
    MetaStatesResponse,
    PendingDecisionView,
    ProfileMeta,
    ProvenanceProjection,
    RequirementView,
    RunDetailView,
    RunMetrics,
    StatusMeta,
    TerminalVerdictProjection,
    TimelineEventView,
)
from synthetic.generator.last_lightkeeper import (
    CLEAN_CAPTIONS_FILENAME,
    CLEAN_FIXTURE_ID,
    CLEAN_MASTER_FILENAME,
    HERO_FIXTURE_ID,
    MANIFEST_FILENAME,
    SyntheticPackage,
    SyntheticUniverse,
    build_universe,
    snapshot_hashes,
)


ROOT = Path(__file__).resolve().parents[2]
SPEC_SOURCE = ROOT / "specifications" / "northstar" / "source"

DEFAULT_STORE_ROOT = ROOT / ".aircheck" / "store"
DEFAULT_WORKSPACE_ROOT = ROOT / ".aircheck" / "workspace"
DEFAULT_UNIVERSE_ROOT = ROOT / ".aircheck" / "universe"

PROFILE_SPECS = {
    "northstar_broadcast_master_v1": {
        "name": "Northstar Broadcast Master",
        "spec_file": "broadcast_master.md",
        "description": "15 normalized checks · strict broadcast delivery · fictional fixture v1",
        "checks_count": 15,
    },
    "northstar_digital_preview_v1": {
        "name": "Northstar Digital Preview",
        "spec_file": "digital_preview.md",
        "description": "13 normalized checks · digital streaming preview · fictional fixture v1",
        "checks_count": 13,
    },
}

# The synthetic universe is always "The Last Lightkeeper"; the runtime record does
# not carry a program title, so the projection states the truthful synthetic program.
PROGRAM_ID = "the_last_lightkeeper"
PROGRAM_TITLE = "The Last Lightkeeper"

# Durable, non-authority-bearing provenance of the run's bounded S3-diagnosis
# semantic participation (AgentCore vs deterministic fallback). Lives in the run
# workspace so it is mirrored to S3 and survives a container restart (M8 audit P4).
AGENTCORE_PROVENANCE_FILE = "agentcore_provenance.json"

_ARTIFACT_LABELS = {
    "QC_REPORT": ("QC report", "report"),
    "LEDGER_EXPORT": ("Run ledger", "ledger"),
    "LINEAGE": ("Asset lineage", "lineage"),
    "TOOL_OUTPUT": ("Terminal verdict", "terminal"),
    "MANIFEST": ("Delivery manifest", "manifest"),
    "CHECKSUMS": ("Checksums", "checksums"),
    "DERIVATIVE": ("Derivative", "derivative"),
}

_ROLE_LABELS = {
    "PROGRAM_MASTER": "Program master",
    "CAPTIONS": "Captions",
    "MANIFEST": "Manifest",
    "OTHER": "Other",
}


_log = logging.getLogger("aircheck.api")


class StateUnavailableError(RuntimeError):
    """Durable authoritative state could not be established/confirmed (fail closed).

    Raised instead of silently serving stale or partially-hydrated state when a
    required S3 sync fails — a cold container that has never synced, or a mutation
    whose run workspace could not be confirmed. Surfaced as a product-safe 503.
    """


class AIRCheckService:
    """Façade projecting durable M4 runtime truth and delegating commands."""

    def __init__(
        self,
        store_root: Path | None = None,
        workspace_root: Path | None = None,
        universe_root: Path | None = None,
        *,
        s3_client: object | None = None,
        agentcore_client: object | None = None,
    ) -> None:
        self.store_root = Path(
            os.environ.get("AIRCHECK_STORE_ROOT", store_root or DEFAULT_STORE_ROOT)
        )
        self.workspace_root = Path(
            os.environ.get(
                "AIRCHECK_WORKSPACE_ROOT", workspace_root or DEFAULT_WORKSPACE_ROOT
            )
        )
        self.universe_root = Path(
            os.environ.get(
                "AIRCHECK_UNIVERSE_ROOT", universe_root or DEFAULT_UNIVERSE_ROOT
            )
        )
        self.store_root.mkdir(parents=True, exist_ok=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        # Persistence boundary. When AIRCHECK_S3_BUCKET is set (production/M7),
        # the durable system of record is S3: the local roots become a disposable
        # cache mirrored to/from S3 (ARCHITECTURE.md persistence; DECISIONS D-007).
        # The deterministic runtime is unchanged — the HeadlessOrchestrator still
        # writes through the same local store_root, and sync_up()/sync_down()
        # only mirror the on-disk cache, so no authority/recovery/terminal logic
        # moves into S3. Locally (no bucket) behaviour is exactly the M5 store.
        self._s3_bucket = os.environ.get("AIRCHECK_S3_BUCKET") or None
        self._s3_prefix = os.environ.get("AIRCHECK_S3_PREFIX", "aircheck").strip("/")
        self._workspace_mirror = None
        self._lease = None
        # Cold-start / fail-closed bookkeeping (M8): the store index is the small
        # authoritative run tree that must hydrate before serving; the workspace
        # (media/evidence) is hydrated lazily per accessed run. ``_index_ever_ok``
        # lets a warm container keep serving last-known state on a transient
        # re-sync blip while a cold container that has never synced fails closed.
        self._index_ever_ok = not bool(self._s3_bucket)
        if self._s3_bucket:
            from aircheck.persistence.s3_lock import S3RunLease
            from aircheck.persistence.s3_store import S3RunStore, S3StateMirror

            self.store = S3RunStore(
                self.store_root,
                bucket=self._s3_bucket,
                prefix=f"{self._s3_prefix}/store",
                s3_client=s3_client,
            )
            self._workspace_mirror = S3StateMirror(
                self.workspace_root,
                self._s3_bucket,
                f"{self._s3_prefix}/workspace",
                s3_client=s3_client,
            )
            self._lease = S3RunLease(
                self._s3_bucket,
                prefix=f"{self._s3_prefix}/locks",
                s3_client=s3_client,
                ttl_s=float(os.environ.get("AIRCHECK_LEASE_TTL_S", "120") or "120"),
            )
        else:
            self.store = LocalDurableStore(self.store_root)

        # Semantic boundary. The bounded S3 diagnose/select/escalate step may run
        # on a Strands agent hosted on Bedrock AgentCore Runtime when configured;
        # its untrusted recommendation is validated by diagnose_findings and falls
        # back visibly to the deterministic double. S1/S2 always stay on the double.
        self._agentcore_arn = os.environ.get("AIRCHECK_AGENTCORE_RUNTIME_ARN") or None
        self._agentcore_region = os.environ.get("AWS_REGION", "us-east-1")
        self._agentcore_model_id = os.environ.get(
            "AIRCHECK_AGENTCORE_MODEL_ID", "us.amazon.nova-lite-v1:0"
        )
        # Injectable data-plane client for the AgentCore semantic model (tests pass a
        # fake; production passes None so boto3 constructs the real client lazily).
        self._agentcore_client = agentcore_client
        self._last_semantic_model = None

        self._universe: SyntheticUniverse | None = None

    # ------------------------------------------------------------- persistence

    @property
    def s3_enabled(self) -> bool:
        return self._s3_bucket is not None

    @property
    def agentcore_enabled(self) -> bool:
        return self._agentcore_arn is not None

    def sync_index(self, *, strict: bool = False) -> None:
        """Hydrate the small authoritative run index from S3 (store tree only).

        This is the per-request/cold-start hydration path (M8): it transfers only
        the run snapshots/events/ledger/authorizations (~1 MB for the demo), never
        the ~100 MB media/evidence workspace, which is hydrated lazily per accessed
        run by :meth:`hydrate_run`. No-op locally.

        Fail closed, not silent: a sync failure is logged. ``strict`` (used under a
        mutation lease to re-confirm authoritative state) raises
        :class:`StateUnavailableError` on any failure. Otherwise, a container that
        has never established authoritative state raises (503) rather than serve an
        empty/stale index as if current, while a warm container that already synced
        once keeps serving its last-known index on a transient blip (the mutation
        path re-confirms strictly under the per-run lease).
        """
        if not self.s3_enabled:
            return
        try:
            self.store.sync_down()  # type: ignore[attr-defined]
            self._index_ever_ok = True
        except Exception as exc:
            _log.error(
                "aircheck.s3.index_sync_failed error=%s ever_ok=%s strict=%s",
                type(exc).__name__,
                self._index_ever_ok,
                strict,
            )
            if strict or not self._index_ever_ok:
                raise StateUnavailableError(
                    "AIRCheck durable state is temporarily unavailable."
                ) from exc

    def hydrate_run(self, run_id: str, *, strict: bool = False) -> None:
        """Lazily hydrate one run's media/evidence workspace from S3 (no-op locally).

        Only the accessed run's subtree transfers, so a cold container never pulls
        unrelated historical media. Incremental (content-hash) so a warm container
        re-checks cheaply and only fetches changed objects. On failure it logs; a
        mutation (``strict=True``) fails closed with :class:`StateUnavailableError`
        rather than operate on unconfirmed media, while a read degrades truthfully
        (the already-synced store snapshot governs; a missing artifact 404s).
        """
        if not self.s3_enabled or self._workspace_mirror is None:
            return
        try:
            self._workspace_mirror.sync_down(subpath=run_id)
        except Exception as exc:
            _log.error(
                "aircheck.s3.run_hydrate_failed run=%s error=%s strict=%s",
                run_id,
                type(exc).__name__,
                strict,
            )
            if strict:
                raise StateUnavailableError(
                    "AIRCheck could not confirm durable state for this run."
                ) from exc

    def sync_up_run(self, run_id: str) -> None:
        """Persist a mutated run to S3: the store index plus that run's workspace.

        The store sync is incremental over the small index; the workspace sync is
        scoped to the mutated run so a decision never rescans the whole media tree.
        No-op locally.
        """
        if not self.s3_enabled:
            return
        self.store.sync_up()  # type: ignore[attr-defined]
        if self._workspace_mirror is not None:
            self._workspace_mirror.sync_up(subpath=run_id)

    def _mutation_lease(self, run_id: str):
        """Exclusive per-run lease for a mutation (no-op context locally)."""
        if self._lease is None:
            return nullcontext()
        return self._lease.hold(run_id)

    # --------------------------------------------------------- agentcore provenance

    def _record_provenance(self, run_id: str, model: object) -> None:
        """Append this run's S3-diagnosis invocation provenance to a durable sidecar.

        Reads the semantic model's product-safe invocation records (only present on
        the AgentCore-backed model; the pure double emits none) and merges them into
        ``workspace/<run_id>/agentcore_provenance.json`` by opaque session id, so a
        run that diagnoses across create + post-approval accumulates a full record.
        Non-authority-bearing evidence: it never touches measurements, predicates,
        authorization, or the terminal verdict.
        """
        records = getattr(model, "invocations", None)
        if not records:
            return
        path = self.workspace_root / run_id / AGENTCORE_PROVENANCE_FILE
        existing: list[dict] = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8")).get(
                    "invocations", []
                )
            except Exception:
                existing = []
        seen = {r.get("session_id") for r in existing if isinstance(r, dict)}
        merged = list(existing)
        for record in records:
            entry = record.as_log()
            entry.pop("event", None)
            if entry.get("session_id") not in seen:
                merged.append(entry)
                seen.add(entry.get("session_id"))
        source = (
            "AGENTCORE"
            if any(r.get("source") == "AGENTCORE" for r in merged)
            else "FALLBACK"
        )
        runtime = next(
            (r.get("runtime") for r in reversed(merged) if r.get("runtime")), None
        )
        doc = {
            "run_id": run_id,
            "semantic_source": source,
            "model_id": self._agentcore_model_id,
            "runtime": runtime,
            "invocations": merged,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _load_provenance(self, run_id: str) -> AgentCoreProvenance | None:
        """Project the durable S3-diagnosis provenance sidecar, if present."""
        path = self.workspace_root / run_id / AGENTCORE_PROVENANCE_FILE
        if not path.exists():
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        invocations = [
            AgentCoreInvocationRecord(
                source=str(r.get("source", "")),
                session_id=str(r.get("session_id", "")),
                model_id=str(r.get("model_id", "")),
                runtime=r.get("runtime"),
                latency_ms=r.get("latency_ms"),
                findings=r.get("findings"),
                dispositions=list(r.get("dispositions", []) or []),
                fallback_reason=r.get("fallback_reason"),
            )
            for r in doc.get("invocations", [])
            if isinstance(r, dict)
        ]
        return AgentCoreProvenance(
            semantic_source=str(doc.get("semantic_source", "FALLBACK")),
            model_id=str(doc.get("model_id", self._agentcore_model_id)),
            runtime=doc.get("runtime"),
            invocations=invocations,
        )

    def _build_semantic_model(self, profile_id: str, segments):
        """Return the semantic model for a run.

        AgentCore for the S3 ``Diagnosis`` output when configured (with a visible
        deterministic fallback), the provider-free double otherwise. The double is
        always the fallback so authority and the hero survive any AgentCore fault.
        """
        double = DemoSemanticModel(profile_id=profile_id, segments=segments)
        if not self.agentcore_enabled:
            self._last_semantic_model = double
            return double
        from aircheck.agent.agentcore_model import AgentCoreSemanticModel

        model = AgentCoreSemanticModel(
            fallback=double,
            runtime_arn=self._agentcore_arn,
            region=self._agentcore_region,
            model_id=self._agentcore_model_id,
            client=self._agentcore_client,
        )
        self._last_semantic_model = model
        return model

    # ---------------------------------------------------------------- universe

    def get_universe(self) -> SyntheticUniverse:
        if self._universe is None:
            clean_dir = self.universe_root / "clean_broadcast"
            hero_dir = self.universe_root / "hero_faults"
            gt = self.universe_root / "m2_ground_truth.json"
            if clean_dir.exists() and hero_dir.exists() and gt.exists():
                clean_orig = clean_dir / "originals"
                hero_orig = hero_dir / "originals"
                clean_pkg = SyntheticPackage(
                    fixture_id=CLEAN_FIXTURE_ID,
                    root=clean_dir,
                    originals=clean_orig,
                    master=clean_orig / CLEAN_MASTER_FILENAME,
                    captions=clean_orig / CLEAN_CAPTIONS_FILENAME,
                    manifest=clean_orig / MANIFEST_FILENAME,
                    source_fixture_id=None,
                    source_asset_hashes=MappingProxyType(snapshot_hashes(clean_orig)),
                    fault_ledger=None,
                )
                # Discover the hero originals by extension rather than hard-coding
                # names. The hero master/captions filenames are generator-derived
                # (e.g. the-last-lightkeeper-delivery-v1.mov/.srt); this branch runs
                # only when the universe is pre-baked (production/M7), so it must
                # match whatever build_universe actually wrote.
                hero_master = next(hero_orig.glob("*.mov"))
                hero_captions = next(hero_orig.glob("*.srt"))
                hero_pkg = SyntheticPackage(
                    fixture_id=HERO_FIXTURE_ID,
                    root=hero_dir,
                    originals=hero_orig,
                    master=hero_master,
                    captions=hero_captions,
                    manifest=None,
                    source_fixture_id=clean_pkg.fixture_id,
                    source_asset_hashes=clean_pkg.source_asset_hashes,
                    fault_ledger=hero_dir / "faults.json",
                )
                self._universe = SyntheticUniverse(
                    root=self.universe_root,
                    clean=clean_pkg,
                    hero=hero_pkg,
                    ground_truth=gt,
                )
            else:
                if self.universe_root.exists():
                    shutil.rmtree(self.universe_root)
                self._universe = build_universe(self.universe_root)
        return self._universe

    # ------------------------------------------------------------ meta states

    def get_meta_states(self) -> MetaStatesResponse:
        run_statuses = [
            StatusMeta(
                name=status.value,
                label=status.value.replace("_", " ").title(),
                badge_variant=(
                    "pass"
                    if status is RunStatus.DELIVERY_READY
                    else "decision"
                    if status is RunStatus.AWAITING_HUMAN_DECISION
                    else "fail"
                    if status in {RunStatus.BLOCKED, RunStatus.FAILED}
                    else "neutral"
                ),
                terminal=status in TERMINAL_STATUSES,
                description=f"AIRCheck operational state: {status.value}",
            )
            for status in RunStatus
        ]

        product_statuses = [
            StatusMeta(
                name="DELIVERY READY",
                label="Delivery Ready",
                badge_variant="pass",
                terminal=True,
                description="Authoritative terminal verdict is DELIVERY_READY with verified original integrity.",
            ),
            StatusMeta(
                name="DECISION REQUIRED",
                label="Decision Required",
                badge_variant="decision",
                terminal=False,
                description="Execution reached an authority boundary; a human decision is pending.",
            ),
            StatusMeta(
                name="PASS",
                label="Pass",
                badge_variant="pass",
                terminal=False,
                description="Requirement satisfied by an inspected media fact (predicate PASS).",
            ),
            StatusMeta(
                name="FIXED",
                label="Fixed",
                badge_variant="pass",
                terminal=False,
                description="Defect remedied by a verified autonomous or authorized action and re-inspected.",
            ),
            StatusMeta(
                name="FAIL",
                label="Fail",
                badge_variant="fail",
                terminal=False,
                description="Requirement violated, unresolved, or not evaluated; never rendered as readiness.",
            ),
        ]

        profiles = [
            ProfileMeta(
                profile_id=pid,
                profile_name=info["name"],
                description=info["description"],
                checks_count=info["checks_count"],
            )
            for pid, info in PROFILE_SPECS.items()
        ]

        return MetaStatesResponse(
            run_statuses=run_statuses,
            product_statuses=product_statuses,
            terminal_outcomes=[item.value for item in TerminalOutcome],
            profiles=profiles,
        )

    # --------------------------------------------------------- run lifecycle

    def _get_document(self, run_id: str, profile_id: str):
        spec_info = PROFILE_SPECS.get(profile_id)
        if not spec_info:
            raise ValueError(f"Unknown destination profile: {profile_id}")
        source_path = SPEC_SOURCE / spec_info["spec_file"]
        return ingest_source_document(
            doc_id=f"doc_{run_id.removeprefix('run_')}",
            run_id=run_id,
            kind=SourceKind.PROFILE,
            title=profile_id,
            raw=source_path.read_bytes(),
            ingested_at=datetime.now(UTC),
        )

    def _package_inputs(self, package) -> tuple[PackageAssetInput, ...]:
        values = [
            PackageAssetInput(
                filename=package.master.name, role=AssetRole.PROGRAM_MASTER
            ),
            PackageAssetInput(
                filename=package.captions.name, role=AssetRole.CAPTIONS
            ),
        ]
        if package.manifest is not None:
            values.append(
                PackageAssetInput(
                    filename=package.manifest.name, role=AssetRole.MANIFEST
                )
            )
        return tuple(values)

    def _semantic_model(self, run_id: str, profile_id: str):
        document = self._get_document(run_id, profile_id)
        return self._build_semantic_model(
            profile_id, segment_source_document(document)
        )

    def seed_canonical_runs(self) -> None:
        """Seed the canonical demonstration runs once, if the store is empty.

        Both runs are produced by the real deterministic runtime over the frozen
        synthetic universe: the Broadcast hero pauses at the Tier-2 authority
        boundary and the Preview run earns DELIVERY_READY autonomously.
        """
        runs_dir = self.store_root / "runs"
        if runs_dir.exists() and any(runs_dir.iterdir()):
            return
        universe = self.get_universe()
        self._execute_new_run(
            run_id="run_llk_broadcast_042",
            profile_id="northstar_broadcast_master_v1",
            package=universe.hero,
        )
        self._execute_new_run(
            run_id="run_llk_preview_018",
            profile_id="northstar_digital_preview_v1",
            package=universe.hero,
        )

    def _execute_new_run(
        self,
        *,
        run_id: str,
        profile_id: str,
        package,
    ) -> str:
        workspace = self.workspace_root / run_id
        workspace.mkdir(parents=True, exist_ok=True)
        document = self._get_document(run_id, profile_id)
        model = self._build_semantic_model(
            profile_id, segment_source_document(document)
        )
        orchestrator = HeadlessOrchestrator(
            workspace=workspace,
            store_root=self.store_root,
        )
        profile_name = PROFILE_SPECS[profile_id]["name"]
        state = orchestrator.start(
            run_id=run_id,
            source_root=package.originals,
            assets=self._package_inputs(package),
            document=document,
            content_type="program",
            profile_id=profile_id,
            profile_name=profile_name,
            semantic_model=model,
        )
        for _ in range(8):
            if state.status in {
                RunStatus.AWAITING_HUMAN_DECISION,
                RunStatus.DELIVERY_READY,
                RunStatus.BLOCKED,
                RunStatus.FAILED,
            }:
                break
            state = orchestrator.advance(model)
        return run_id

    def create_run(self, req: CreateRunRequest) -> CreateRunResponse:
        if req.profile_id not in PROFILE_SPECS:
            raise ValueError(f"Invalid profile_id: {req.profile_id}")
        universe = self.get_universe()
        package = universe.hero if req.package_type == "hero" else universe.clean
        prefix = "broadcast" if "broadcast" in req.profile_id else "preview"
        run_id = f"run_llk_{prefix}_{datetime.now(UTC):%H%M%S}_{uuid.uuid4().hex[:6]}"
        self._execute_new_run(
            run_id=run_id,
            profile_id=req.profile_id,
            package=package,
        )
        self._record_provenance(run_id, self._last_semantic_model)
        snapshot = self.store.load_snapshot(run_id)
        self.sync_up_run(run_id)
        profile_name = PROFILE_SPECS[req.profile_id]["name"]
        return CreateRunResponse(
            run_id=run_id,
            status=snapshot.status.value,
            profile_id=req.profile_id,
            profile_name=profile_name,
            message=f"Run {run_id} executed to {snapshot.status.value}.",
        )

    # ------------------------------------------------------- truth helpers

    def _final_predicate(self, run: RuntimeRun, requirement_id: str):
        preds = [p for p in run.predicates if p.requirement_id == requirement_id]
        return max(preds, key=lambda p: p.cycle) if preds else None

    def _final_measurement(self, run: RuntimeRun, requirement_id: str):
        ms = [m for m in run.measurements if m.requirement_id == requirement_id]
        return max(ms, key=lambda m: m.cycle) if ms else None

    def _has_pending_decision(self, run: RuntimeRun, requirement_id: str) -> bool:
        """True when an OPEN finding for this requirement awaits a PENDING decision.

        A requirement can accumulate more than one finding across cycles (for
        example a stale RESOLVED finding from an earlier attempt plus the current
        OPEN finding paused at a Tier-2 boundary). The pending state is therefore
        proven against the live decision request and its OPEN finding, not the
        first finding that happens to reference the requirement.
        """
        return any(
            dr.status is DecisionRequestStatus.PENDING
            and any(
                f.finding_id == dr.finding_id
                and f.requirement_id == requirement_id
                and f.status is FindingLifecycle.OPEN
                for f in run.findings
            )
            for dr in run.decision_requests
        )

    def _verified_count(self, run: RuntimeRun) -> int:
        """Requirements whose latest authoritative predicate outcome is PASS."""
        return sum(
            1
            for req in run.requirements
            if (p := self._final_predicate(run, req.requirement_id)) is not None
            and p.outcome is PredicateOutcome.PASS
        )

    def _unresolved_blockers(self, run: RuntimeRun) -> int:
        """Blocking requirements not proven PASS by the latest predicate."""
        count = 0
        for req in run.requirements:
            pred = self._final_predicate(run, req.requirement_id)
            passed = pred is not None and pred.outcome is PredicateOutcome.PASS
            if not passed:
                count += 1
        return count

    def _requirement_status(
        self, run: RuntimeRun, snapshot, req: RuntimeRequirement
    ) -> tuple[str, str | None]:
        """Project the product status and raw predicate outcome from real truth.

        The latest authoritative predicate outcome anchors the status: a
        requirement is PASS or FIXED only when its most recent predicate is PASS.
        A requirement paused at a live human-decision boundary is DECISION
        REQUIRED; anything otherwise unproven is FAIL. A stale RESOLVED finding
        from an earlier cycle can never on its own paint a still-failing
        requirement green — the runtime's most recent predicate governs.
        """
        pred = self._final_predicate(run, req.requirement_id)
        outcome = pred.outcome.value if pred is not None else None
        passed = pred is not None and pred.outcome is PredicateOutcome.PASS

        if snapshot.status is RunStatus.AWAITING_HUMAN_DECISION and self._has_pending_decision(
            run, req.requirement_id
        ):
            return "DECISION REQUIRED", outcome

        if passed:
            resolved = any(
                f.requirement_id == req.requirement_id
                and f.status is FindingLifecycle.RESOLVED
                for f in run.findings
            )
            return ("FIXED" if resolved else "PASS"), outcome

        return "FAIL", outcome

    def _observed_text(self, run: RuntimeRun, req: RuntimeRequirement) -> str:
        m = self._final_measurement(run, req.requirement_id)
        if m is None:
            return req.normalization_status.value.replace("_", " ").title()
        if m.status.value == "ERROR" or m.value is None:
            return m.error or "Not evaluated"
        unit = f" {m.unit}" if m.unit else ""
        return f"{m.value}{unit}"

    def _expected_text(self, req: RuntimeRequirement) -> str:
        if req.constraint is not None:
            return render_constraint(req.constraint, label="Requires")
        return req.rendered_text or "Strict destination match"

    def _constraint_delta(self, run: RuntimeRun, req: RuntimeRequirement) -> str | None:
        """Truthful signed distance from the real constraint, or None."""
        m = self._final_measurement(run, req.requirement_id)
        c = req.constraint
        if m is None or c is None or not isinstance(m.value, (int, float)):
            return None
        value = float(m.value)
        unit = m.unit or c.unit or ""
        if isinstance(c, RangeConstraint):
            if value > c.upper:
                return f"+{value - c.upper:.2f} {unit} above the {c.upper:g} {unit} ceiling"
            if value < c.lower:
                return f"{value - c.lower:.2f} {unit} below the {c.lower:g} {unit} floor"
            return None
        if isinstance(c, MaxConstraint) and value > float(c.value):
            return f"+{value - float(c.value):.2f} {unit} above the {c.value:g} {unit} maximum"
        if isinstance(c, MinConstraint) and value < float(c.value):
            return f"{value - float(c.value):.2f} {unit} below the {c.value:g} {unit} minimum"
        if isinstance(c, EqualsConstraint):
            diff = value - float(c.value)
            if abs(diff) < 1e-9:
                return None
            return f"{diff:+.2f} {unit} from the required {c.value:g} {unit}"
        return None

    # ------------------------------------------------------------ deliveries

    def list_runs(self) -> DeliveriesResponse:
        runs_dir = self.store_root / "runs"
        if not runs_dir.exists():
            return DeliveriesResponse(
                runs=[],
                counts=DeliveriesCounts(
                    active_runs=0,
                    decision_required=0,
                    delivery_ready=0,
                    unresolved_blockers=0,
                ),
            )

        runs: list[DeliverySummary] = []
        for path in sorted(
            runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            if not path.is_dir() or not (path / "snapshot.json").exists():
                continue
            run_id = path.name
            try:
                snapshot = self.store.load_snapshot(run_id)
                run = self.store.load_runtime_run(run_id)
            except Exception:
                continue

            terminal_proj = self._terminal_projection(snapshot)
            display_status = self._display_status(snapshot, terminal_proj)
            total_reqs = len(run.requirements)
            verified_count = self._verified_count(run)

            pending_decisions = sum(
                1
                for dr in run.decision_requests
                if dr.status is DecisionRequestStatus.PENDING
            )
            if display_status == "DECISION REQUIRED":
                finding_summary = (
                    f"{pending_decisions} open decision"
                    f"{'' if pending_decisions == 1 else 's'}"
                )
            elif display_status == "DELIVERY READY":
                finding_summary = f"All {total_reqs} checks verified"
            elif display_status in {"BLOCKED", "FAIL"}:
                blockers = self._unresolved_blockers(run)
                finding_summary = (
                    f"{blockers} unresolved blocker{'' if blockers == 1 else 's'}"
                )
            elif display_status == "UNVERIFIED":
                finding_summary = "Verdict pending deterministic verification"
            else:
                finding_summary = f"{verified_count} of {total_reqs} verified"

            updated_at = datetime.fromtimestamp(
                path.stat().st_mtime, tz=UTC
            ).strftime("%H:%M:%S UTC")
            profile_name = PROFILE_SPECS.get(run.profile_id, {}).get(
                "name", run.profile_id
            )

            runs.append(
                DeliverySummary(
                    run_id=run_id,
                    program_id=PROGRAM_ID,
                    program_title=PROGRAM_TITLE,
                    destination_profile=profile_name,
                    profile_id=run.profile_id,
                    run_state=snapshot.status.value,
                    display_status=display_status,
                    finding_summary=finding_summary,
                    verified=verified_count,
                    total=total_reqs,
                    updated_at=updated_at,
                    terminal_verdict=terminal_proj,
                )
            )

        terminal_values = {s.value for s in TERMINAL_STATUSES}
        active = sum(1 for r in runs if r.run_state not in terminal_values)
        dec_req = sum(
            1 for r in runs if r.run_state == RunStatus.AWAITING_HUMAN_DECISION.value
        )
        ready = sum(
            1
            for r in runs
            if r.terminal_verdict is not None
            and r.terminal_verdict.outcome == TerminalOutcome.DELIVERY_READY.value
        )
        blockers = sum(
            1
            for r in runs
            if (
                r.terminal_verdict is not None
                and r.terminal_verdict.outcome == TerminalOutcome.BLOCKED.value
            )
            or r.run_state in {RunStatus.BLOCKED.value, RunStatus.FAILED.value}
        )

        return DeliveriesResponse(
            runs=runs,
            counts=DeliveriesCounts(
                active_runs=active,
                decision_required=dec_req,
                delivery_ready=ready,
                unresolved_blockers=blockers,
            ),
        )

    def _terminal_projection(self, snapshot) -> TerminalVerdictProjection | None:
        tv = snapshot.terminal_verdict
        if tv is None:
            return None
        return TerminalVerdictProjection(
            outcome=tv.outcome.value,
            cycle=tv.cycle,
            reasons=list(tv.reasons),
            computed_at=tv.computed_at.isoformat(),
            originals_integrity_verified=tv.originals_integrity_verified,
        )

    def _display_status(
        self, snapshot, terminal_proj: TerminalVerdictProjection | None
    ) -> str:
        """Product display label obeying the critical readiness invariant.

        Green readiness is presented ONLY when the authoritative terminal verdict
        is DELIVERY_READY. A DELIVERY_READY run state with a null/missing verdict
        is never rendered green.
        """
        if (
            terminal_proj is not None
            and terminal_proj.outcome == TerminalOutcome.DELIVERY_READY.value
        ):
            return "DELIVERY READY"
        if snapshot.status is RunStatus.AWAITING_HUMAN_DECISION:
            return "DECISION REQUIRED"
        # A blocked run is a truthful terminal failure whether the block was
        # computed as a TerminalVerdict or reached directly by a human denial
        # (which routes AWAITING_HUMAN_DECISION -> BLOCKED without a verdict).
        if snapshot.status is RunStatus.BLOCKED:
            return "BLOCKED"
        if snapshot.status is RunStatus.FAILED:
            return "FAIL"
        if snapshot.status is RunStatus.DELIVERY_READY and terminal_proj is None:
            # Critical readiness invariant: status says ready but no authoritative
            # terminal verdict exists — never green.
            return "UNVERIFIED"
        return "INSPECTING"

    # ---------------------------------------------------------- run detail

    def get_run(self, run_id: str) -> RunDetailView:
        # Lazily hydrate only this run's media/evidence workspace (M8 cold-start
        # fix); the store snapshot is already current via the per-request index sync.
        self.hydrate_run(run_id)
        snapshot = self.store.load_snapshot(run_id)
        run = self.store.load_runtime_run(run_id)
        profile_info = PROFILE_SPECS.get(
            run.profile_id,
            {"name": run.profile_id, "checks_count": len(run.requirements)},
        )
        terminal_proj = self._terminal_projection(snapshot)

        req_views: list[RequirementView] = []
        passing = remediated = pending = failed = 0
        for req in run.requirements:
            status, outcome = self._requirement_status(run, snapshot, req)
            if status == "PASS":
                passing += 1
            elif status == "FIXED":
                remediated += 1
            elif status == "DECISION REQUIRED":
                pending += 1
            else:
                failed += 1
            provenance_proj = None
            if req.provenance is not None:
                provenance_proj = ProvenanceProjection(
                    quote=req.provenance.quote,
                    document_title=profile_info.get("name", req.provenance.doc_id),
                    line_start=req.provenance.start,
                    line_end=req.provenance.end,
                )
            req_views.append(
                RequirementView(
                    id=req.requirement_id,
                    category=self._category_for_requirement(req),
                    description=req.rendered_text or req.requirement_id,
                    observed=self._observed_text(run, req),
                    expected=self._expected_text(req),
                    status=status,
                    severity=req.severity.value,
                    predicate_outcome=outcome,
                    source_reference=self._source_reference(profile_info, req),
                    provenance=provenance_proj,
                    disposition=req.disposition.value,
                )
            )

        events: list[TimelineEventView] = []
        for ev in self.store.list_events(run_id):
            summary, ev_ref = self._event_summary_and_evidence(ev)
            events.append(
                TimelineEventView(
                    id=ev.event_id,
                    seq=ev.seq,
                    time=ev.timestamp.strftime("%H:%M:%S UTC"),
                    type=ev.type.value,
                    actor=ev.actor,
                    summary=ev.summary or summary,
                    evidence=ev_ref,
                )
            )

        workspace = self.workspace_root / run_id
        asset_views: list[AssetView] = []
        for ra in run.assets:
            file_path = workspace / ra.relative_path
            detail_info = f"Role: {ra.asset.role.value}"
            sha = None
            if file_path.exists() and file_path.is_file():
                size_kb = file_path.stat().st_size // 1024
                detail_info = f"{ra.asset.role.value} · {size_kb} KB"
                try:
                    sha = hash_file(file_path)[:12]
                    detail_info += f" · SHA {sha}…"
                except Exception:
                    pass
            is_original = ra.asset.protection.value == "ORIGINAL"
            prov_label = "ORIGINAL · PRESERVED" if is_original else "DELIVERY DERIVATIVE"
            relation = ra.asset.relation.value if ra.asset.relation is not None else None
            if is_original or relation in {None, "COPIED_FROM"}:
                asset_status = "PASS"
            else:
                asset_status = "FIXED"
            asset_views.append(
                AssetView(
                    id=ra.asset.asset_id,
                    filename=ra.asset.filename,
                    role=_ROLE_LABELS.get(ra.asset.role.value, ra.asset.role.value),
                    detail=detail_info,
                    provenance=prov_label,
                    status=asset_status,
                    sha256=sha,
                )
            )

        pending_view = self._build_pending_decision_view(run, snapshot)
        display_status = self._display_status(snapshot, terminal_proj)
        evidence_dir = workspace / "evidence"
        evidence_available = bool(run.evidence) or (
            evidence_dir.exists() and any(evidence_dir.iterdir())
        )

        return RunDetailView(
            run_id=run_id,
            program_id=PROGRAM_ID,
            program_title=PROGRAM_TITLE,
            destination_profile=profile_info["name"],
            profile_id=run.profile_id,
            run_state=snapshot.status.value,
            display_status=display_status,
            cycle=snapshot.cycle,
            terminal_verdict=terminal_proj,
            pending_decision=pending_view,
            requirements=req_views,
            events=events,
            assets=asset_views,
            metrics=RunMetrics(
                requirements_total=len(run.requirements),
                passing=passing,
                remediated=remediated,
                pending=pending,
                failed=failed,
            ),
            evidence_available=evidence_available,
        )

    def _source_reference(self, profile_info: dict, req: RuntimeRequirement) -> str:
        name = profile_info.get("name", "Northstar")
        if req.provenance is not None and req.provenance.reference:
            return f"{name} § {req.provenance.reference}"
        return f"{name} § {req.requirement_id}"

    # ------------------------------------------------------------- decision

    def _build_pending_decision_view(
        self, run: RuntimeRun, snapshot
    ) -> PendingDecisionView | None:
        if snapshot.status is not RunStatus.AWAITING_HUMAN_DECISION:
            return None
        try:
            request = self.store.load_pending_decision(snapshot.run_id)
        except Exception:
            return None
        return self._decision_view(run, request)

    def _decision_view(self, run: RuntimeRun, request) -> PendingDecisionView:
        finding = next(
            (f for f in run.findings if f.finding_id == request.finding_id), None
        )
        option = None
        if finding is not None:
            option = next(
                (o for o in finding.options if o.option_id == request.option_id), None
            )
        requirement = None
        asset = None
        if finding is not None:
            requirement = next(
                (r for r in run.requirements if r.requirement_id == finding.requirement_id),
                None,
            )
            asset = next(
                (a for a in run.assets if a.asset.asset_id == finding.asset_id), None
            )
        # The tool/tier come from the bound option while it is exposed. Denial
        # clears the finding's options, so a resolved-denied view truthfully omits
        # the tool rather than reconstructing it.
        tool = option.tool if option is not None else None
        tier = option.tier if option is not None else None
        if tier is None and request.kind.value == "TIER2_APPROVAL":
            tier = 2

        measurement = (
            self._final_measurement(run, requirement.requirement_id)
            if requirement is not None
            else None
        )
        if measurement is not None and measurement.value is not None:
            unit = f" {measurement.unit}" if measurement.unit else ""
            measured_value = f"{measurement.value}{unit}"
        elif finding is not None:
            measured_value = finding.observed_rendered
        else:
            measured_value = "Not measured"

        if requirement is not None and requirement.constraint is not None:
            required_value = render_constraint(requirement.constraint, label="Requires")
        elif finding is not None:
            required_value = finding.expected_rendered
        else:
            required_value = "Destination requirement"

        delta = (
            self._constraint_delta(run, requirement)
            if requirement is not None
            else None
        )

        proposed_operation = (
            option.description_rendered if option is not None else request.consequences_rendered
        )
        if option is not None:
            args = {a.name: a.value for a in option.tool_args}
            target = args.get("target_lufs")
            tol = args.get("tolerance")
            if target is not None:
                tail = f" Target {target:g} LUFS" + (f" ± {tol:g}" if tol is not None else "") + "."
                proposed_operation = f"{option.description_rendered}{tail}"

        profile_name = PROFILE_SPECS.get(run.profile_id, {}).get("name", "Northstar")
        source_reference = profile_name
        source_quote = None
        requirement_text = ""
        if requirement is not None:
            requirement_text = requirement.rendered_text
            if requirement.provenance is not None:
                source_quote = requirement.provenance.quote
                if requirement.provenance.reference:
                    source_reference = f"{profile_name} § {requirement.provenance.reference}"

        decision = next(
            (d for d in run.decisions if d.decision_id == request.decision_id), None
        )
        resolved = request.status is not DecisionRequestStatus.PENDING

        return PendingDecisionView(
            decision_id=request.decision_id,
            finding_id=request.finding_id,
            option_id=request.option_id,
            status=request.status.value,
            resolved=resolved,
            question=request.question_rendered,
            consequences=request.consequences_rendered,
            tool=tool,
            tier=tier,
            policy_tier=self._tier_label(tier),
            asset_filename=asset.asset.filename if asset is not None else "Delivery asset",
            source_asset_role=(
                _ROLE_LABELS.get(asset.asset.role.value, asset.asset.role.value)
                if asset is not None
                else "Program master"
            ),
            measured_value=measured_value,
            required_value=required_value,
            delta=delta,
            proposed_operation=proposed_operation,
            original_preservation=request.consequences_rendered,
            requirement_id=requirement.requirement_id if requirement is not None else request.finding_id,
            requirement_text=requirement_text,
            source_reference=source_reference,
            source_quote=source_quote,
            produces_derivative=bool(option.produces_derivative) if option is not None else False,
            decided_by=decision.actor if decision is not None else None,
            decision_choice=decision.choice.value if decision is not None else None,
        )

    def _tier_label(self, tier: int | None) -> str:
        if tier == 1:
            return "Tier 1 · reversible"
        if tier == 2:
            return "Tier 2 · content-affecting"
        return "Authority boundary"

    def get_decision(self, run_id: str) -> PendingDecisionView:
        snapshot = self.store.load_snapshot(run_id)
        run = self.store.load_runtime_run(run_id)
        view = self._build_pending_decision_view(run, snapshot)
        if view is not None:
            return view
        # Not awaiting: project the most recent resolved decision truthfully, if any.
        if run.decision_requests:
            request = run.decision_requests[-1]
            return self._decision_view(run, request)
        raise ValueError(f"Run {run_id} has no human-decision requirement.")

    def submit_decision(
        self, run_id: str, approved: bool, actor: str = "human:operator"
    ) -> DecideResponse:
        # Fast, product-truthful reject for a run that is not at a decision point
        # in this container's already-synced view (unchanged M5 semantics -> 400).
        snapshot = self.store.load_snapshot(run_id)
        if snapshot.status is not RunStatus.AWAITING_HUMAN_DECISION:
            raise ValueError(
                f"Run {run_id} is in status {snapshot.status.value}, not awaiting decision."
            )

        # Cross-container mutation safety (M8, audit P1). The account concurrency
        # limit means two warm containers can both observe this PENDING decision;
        # last-writer-wins over the S3 mirror would let concurrent APPROVE/DENY both
        # "succeed". Hold an exclusive per-run S3 lease, then re-read the fresh
        # authoritative snapshot UNDER the lease. Exactly one conflicting mutation
        # wins; the loser sees the decision already consumed and conflicts (409),
        # so no second Tier-2 derivative and no duplicate authorization can be
        # minted. A stale writer never overwrites newer state because it never
        # proceeds past this re-check.
        with self._mutation_lease(run_id):
            # Re-confirm authoritative state UNDER the lease: a fresh strict index
            # sync (the snapshot another container may have just resolved) plus this
            # run's media/evidence. Both fail closed rather than mutate on unconfirmed
            # state.
            self.sync_index(strict=True)
            self.hydrate_run(run_id, strict=True)
            snapshot = self.store.load_snapshot(run_id)
            if snapshot.status is not RunStatus.AWAITING_HUMAN_DECISION:
                raise ConflictError(
                    "This decision was already resolved. Refresh to see the current run state."
                )
            pending_id = snapshot.pending_decision_id
            workspace = self.workspace_root / run_id
            orchestrator = HeadlessOrchestrator(
                workspace=workspace,
                store_root=self.store_root,
                run_id=run_id,
            )
            resolved_snapshot = orchestrator.resolve_decision(approved=approved, actor=actor)
            if approved:
                model = self._semantic_model(
                    run_id, self.store.load_runtime_run(run_id).profile_id
                )
                for _ in range(8):
                    if resolved_snapshot.status in {
                        RunStatus.DELIVERY_READY,
                        RunStatus.BLOCKED,
                        RunStatus.FAILED,
                    }:
                        break
                    resolved_snapshot = orchestrator.advance(model)
                self._record_provenance(run_id, model)
            self.sync_up_run(run_id)
        choice_str = "APPROVED" if approved else "DENIED"
        return DecideResponse(
            run_id=run_id,
            decision_id=pending_id or "decision",
            status="RESOLVED",
            choice=choice_str,
            next_status=resolved_snapshot.status.value,
            message=(
                f"Decision {choice_str.lower()}. The runtime advanced the run to "
                f"{resolved_snapshot.status.value}."
            ),
        )

    # ------------------------------------------------------------- evidence

    def get_evidence(self, run_id: str) -> EvidenceResponse:
        # Lazily hydrate this run's evidence workspace before projecting it (M8).
        self.hydrate_run(run_id)
        snapshot = self.store.load_snapshot(run_id)
        run = self.store.load_runtime_run(run_id)
        workspace = self.workspace_root / run_id
        tv_proj = self._terminal_projection(snapshot)

        artifacts: list[EvidenceArtifactView] = []
        for ev in run.evidence:
            filename = ev.relative_path.split("/")[-1]
            file_path = workspace / ev.relative_path
            if not file_path.exists() or not file_path.is_file():
                continue
            title, icon = _ARTIFACT_LABELS.get(ev.kind.value, (filename, "generic"))
            size_bytes = file_path.stat().st_size
            artifacts.append(
                EvidenceArtifactView(
                    name=title,
                    filename=filename,
                    size_bytes=size_bytes,
                    meta=f"{size_bytes} bytes · SHA-256 {ev.sha256[:12]}…",
                    download_url=f"/runs/{run_id}/evidence/{filename}",
                    icon_type=icon,
                )
            )

        profile_info = PROFILE_SPECS.get(run.profile_id, {"name": run.profile_id})
        verification_rows: list[EvidenceVerificationRow] = []
        for req in run.requirements:
            status, _outcome = self._requirement_status(run, snapshot, req)
            verification_rows.append(
                EvidenceVerificationRow(
                    requirement_id=req.requirement_id,
                    category=self._category_for_requirement(req),
                    observed=self._observed_text(run, req),
                    expected=self._expected_text(req),
                    status=status,
                )
            )

        package_hash = self._package_hash(run, workspace)
        total_reqs = len(run.requirements)
        verified_count = self._verified_count(run)
        open_blockers = self._unresolved_blockers(run)
        earned_at = tv_proj.computed_at if tv_proj is not None else None

        return EvidenceResponse(
            run_id=run_id,
            program_title=PROGRAM_TITLE,
            destination_profile=profile_info.get("name", run.profile_id),
            run_state=snapshot.status.value,
            terminal_verdict=tv_proj,
            earned_at=earned_at,
            package_hash=package_hash,
            requirements_verified=f"{verified_count} / {total_reqs} requirements verified",
            metrics=EvidenceMetrics(
                findings_resolved=run.autonomous_remediations + run.authorized_remediations,
                autonomous_fixes=run.autonomous_remediations,
                human_authorized=run.authorized_remediations,
                open_blockers=open_blockers,
            ),
            artifacts=artifacts,
            verification_rows=verification_rows,
            agentcore_provenance=self._load_provenance(run_id),
        )

    def _package_hash(self, run: RuntimeRun, workspace: Path) -> str | None:
        """Authoritative recorded hash of the current program master, if any."""
        for ra in run.current_assets():
            if ra.asset.role is AssetRole.PROGRAM_MASTER:
                return ra.asset.sha256[:16]
        return None

    def get_evidence_file_path(self, run_id: str, filename: str) -> Path:
        if not filename or ".." in filename or "/" in filename or "\\" in filename:
            raise ValueError("Invalid evidence filename.")
        # Ensure the run's evidence artifacts are present on this container (M8).
        self.hydrate_run(run_id)
        evidence_dir = (self.workspace_root / run_id / "evidence").resolve()
        file_path = (evidence_dir / filename).resolve()
        if not file_path.is_relative_to(evidence_dir):
            raise ValueError("Invalid evidence filename.")
        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError(f"Evidence artifact {filename} is not available.")
        return file_path

    # --------------------------------------------------------- formatting

    def _category_for_requirement(self, req: RuntimeRequirement) -> str:
        key = (req.measurement_key or req.requirement_id).lower()
        if "audio" in key or "loudness" in key:
            return "Audio"
        if "video" in key or "codec" in key or "fps" in key or "frame_rate" in key or "width" in key or "height" in key or "container" in key:
            return "Video"
        if "caption" in key or "sub" in key:
            return "Captions"
        if "manifest" in key or "checksum" in key:
            return "Package"
        if "filename" in key or "name" in key:
            return "Naming"
        return "Technical"

    def _event_summary_and_evidence(self, ev: LedgerEvent) -> tuple[str, str | None]:
        payload = ev.payload
        summary = ev.summary
        ev_ref = None
        if isinstance(payload, ActionCompletedPayload):
            ev_ref = f"action_{payload.action_id[:8]}"
        elif isinstance(payload, ActionStartedPayload):
            summary = f"Action started: {payload.tool}"
            ev_ref = f"action_{payload.action_id[:8]}"
        elif isinstance(payload, ActionFailedPayload):
            summary = f"Action failed: {payload.reason}"
        return summary, ev_ref
