"""Canonical M4 hero, profile divergence, decision, and terminal proof."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aircheck.agent.diagnosis import Diagnosis, DiagnosisRejected
from aircheck.domain.events import (
    ActionCompletedPayload,
    ActionFailedPayload,
    ActionStartedPayload,
)
from aircheck.domain.source import ingest_source_document, segment_source_document
from aircheck.domain.status import RunStatus
from aircheck.domain.types import (
    AssetRole,
    AutomationDisposition,
    FindingLifecycle,
    SourceKind,
    TerminalOutcome,
)
from aircheck.media import hash_file
from aircheck.runtime.orchestrator import HeadlessOrchestrator, PackageAssetInput
from aircheck.runtime.remediation import RemediationExecutor
from synthetic.generator.last_lightkeeper import build_universe
from tests.runtime.conftest import HeroSemanticModel


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "specifications" / "northstar" / "source"


def _document(run_id: str, profile_id: str):
    filename = {
        "northstar_broadcast_master_v1": "broadcast_master.md",
        "northstar_digital_preview_v1": "digital_preview.md",
    }[profile_id]
    return ingest_source_document(
        doc_id=f"doc_{run_id.removeprefix('run_')}",
        run_id=run_id,
        kind=SourceKind.PROFILE,
        title=profile_id,
        raw=(SOURCE_ROOT / filename).read_bytes(),
        ingested_at=datetime(2026, 9, 7, 23, 0, tzinfo=UTC),
    )


def _inputs(package) -> tuple[PackageAssetInput, ...]:
    values = [
        PackageAssetInput(filename=package.master.name, role=AssetRole.PROGRAM_MASTER),
        PackageAssetInput(filename=package.captions.name, role=AssetRole.CAPTIONS),
    ]
    if package.manifest is not None:
        values.append(
            PackageAssetInput(filename=package.manifest.name, role=AssetRole.MANIFEST)
        )
    return tuple(values)


def _start(tmp_path: Path, package, profile_id: str, run_id: str):
    document = _document(run_id, profile_id)
    model = HeroSemanticModel(
        profile_id=profile_id,
        segments=segment_source_document(document),
    )
    orchestrator = HeadlessOrchestrator(
        workspace=tmp_path / "workspace",
        store_root=tmp_path / "store",
    )
    state = orchestrator.start(
        run_id=run_id,
        source_root=package.originals,
        assets=_inputs(package),
        document=document,
        content_type="program",
        profile_id=profile_id,
        profile_name={
            "northstar_broadcast_master_v1": "Northstar Broadcast Master",
            "northstar_digital_preview_v1": "Northstar Digital Preview",
        }[profile_id],
        semantic_model=model,
    )
    return orchestrator, model, state


def _advance_until(orchestrator, model, target: RunStatus, *, limit: int = 8):
    state = orchestrator.load_state()
    for _ in range(limit):
        if state.status is target:
            return state
        state = orchestrator.advance(model)
    raise AssertionError(f"run did not reach {target.value}: {state.status.value}")


def test_broadcast_hero_resumes_after_restart_and_earns_ready(m4_universe, tmp_path: Path) -> None:
    original_hashes = {
        path.name: hash_file(path)
        for path in m4_universe.hero.originals.iterdir()
        if path.is_file()
    }
    orchestrator, model, state = _start(
        tmp_path,
        m4_universe.hero,
        "northstar_broadcast_master_v1",
        "run_m4_broadcast_hero",
    )
    assert state.status is RunStatus.FINDINGS_READY
    initial = orchestrator.load_run()
    assert {
        option.tool
        for finding in initial.findings
        for option in finding.options
    } == {
        "rename_delivery_copy",
        "write_checksum_manifest",
        "convert_caption_format",
        "create_normalized_audio_derivative",
    }

    awaiting = _advance_until(
        orchestrator,
        model,
        RunStatus.AWAITING_HUMAN_DECISION,
    )
    assert awaiting.cycle == 1
    assert orchestrator.load_pending_decision().status.value == "PENDING"
    first_diagnosis = next(
        json.loads(prompt) for output, prompt in model.prompts if output is Diagnosis
    )
    assert {
        option["tier"]
        for finding in first_diagnosis["findings"]
        for option in finding["options"]
    } == {1}
    assert "create_normalized_audio_derivative" not in first_diagnosis["exposed_tools"]
    diagnosis_prompts = [
        json.loads(prompt) for output, prompt in model.prompts if output is Diagnosis
    ]
    assert {
        option["tier"]
        for finding in diagnosis_prompts[-1]["findings"]
        for option in finding["options"]
    } == {2}

    restarted = HeadlessOrchestrator(
        workspace=tmp_path / "workspace",
        store_root=tmp_path / "store",
        run_id="run_m4_broadcast_hero",
    )
    after_approval = restarted.resolve_decision(approved=True, actor="human:mu")
    assert after_approval.status is RunStatus.FINDINGS_READY
    assert after_approval.cycle == 2

    ready = _advance_until(restarted, model, RunStatus.DELIVERY_READY)
    assert ready.cycle == 2
    assert ready.terminal_verdict is not None
    assert ready.terminal_verdict.outcome is TerminalOutcome.DELIVERY_READY
    assert ready.terminal_verdict.originals_integrity_verified is True
    final = restarted.load_run()
    assert final.authorized_remediations == 1
    assert final.autonomous_remediations == 4
    assert final.decision_requests[0].status.value == "APPROVED"
    assert len(final.source_documents) == 1
    assert all(requirement.provenance.quote for requirement in final.requirements)
    assert all(
        hash_file(tmp_path / "workspace" / item.relative_path)
        == original_hashes[item.asset.filename]
        for item in final.assets
        if item.asset.protection.value == "ORIGINAL"
    )
    events = restarted.events()
    starts = [event for event in events if isinstance(event.payload, ActionStartedPayload)]
    completions = [event for event in events if isinstance(event.payload, ActionCompletedPayload)]
    assert len(starts) == len(completions) == 5
    assert all(
        next(event.seq for event in completions if event.payload.action_id == start.payload.action_id)
        > start.seq
        for start in starts
    )
    assert restarted.evidence_paths() == {
        "ledger.json",
        "lineage.json",
        "qc-report.json",
        "terminal.json",
    }
    public_evidence = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "workspace" / "evidence").iterdir()
    )
    assert re.search(r"auth_[A-Za-z0-9_-]+", public_evidence) is None
    assert str(m4_universe.hero.originals) not in public_evidence
    report = json.loads(
        (tmp_path / "workspace" / "evidence" / "qc-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["source_documents"][0]["raw_sha256"]
    assert all(item["provenance"]["quote"] for item in report["requirements"])
    assert report["decision_requests"][0]["status"] == "APPROVED"
    assert report["decisions"][0]["choice"] == "APPROVED"
    assert report["authorizations"][0]["status"] == "CONSUMED"
    assert report["authorizations"][0]["authorization"] == "REDACTED_PRESENT"


def test_broadcast_denial_is_deterministically_blocked(m4_universe, tmp_path: Path) -> None:
    orchestrator, model, _ = _start(
        tmp_path,
        m4_universe.hero,
        "northstar_broadcast_master_v1",
        "run_m4_broadcast_denied",
    )
    _advance_until(orchestrator, model, RunStatus.AWAITING_HUMAN_DECISION)

    blocked = HeadlessOrchestrator(
        workspace=tmp_path / "workspace",
        store_root=tmp_path / "store",
        run_id="run_m4_broadcast_denied",
    ).resolve_decision(approved=False, actor="human:mu")

    assert blocked.status is RunStatus.BLOCKED
    assert blocked.terminal_verdict is None


def test_preview_hero_reaches_ready_without_human_decision(m4_universe, tmp_path: Path) -> None:
    orchestrator, model, _ = _start(
        tmp_path,
        m4_universe.hero,
        "northstar_digital_preview_v1",
        "run_m4_preview_hero",
    )

    ready = _advance_until(orchestrator, model, RunStatus.DELIVERY_READY)

    assert ready.cycle == 1
    assert ready.terminal_verdict is not None
    assert ready.terminal_verdict.outcome is TerminalOutcome.DELIVERY_READY
    run = orchestrator.load_run()
    assert run.decision_requests == ()
    assert run.authorized_remediations == 0
    assert run.autonomous_remediations == 1


def test_clean_broadcast_is_ready_without_remediation(m4_universe, tmp_path: Path) -> None:
    orchestrator, model, state = _start(
        tmp_path,
        m4_universe.clean,
        "northstar_broadcast_master_v1",
        "run_m4_clean_broadcast",
    )
    assert state.status is RunStatus.FINDINGS_READY

    ready = orchestrator.advance(model)

    assert ready.status is RunStatus.DELIVERY_READY
    assert ready.cycle == 0
    assert orchestrator.load_run().findings == ()
    assert not any(
        isinstance(event.payload, ActionStartedPayload)
        for event in orchestrator.events()
    )


def test_terminal_restart_restores_missing_evidence_idempotently(
    m4_universe,
    tmp_path: Path,
) -> None:
    orchestrator, model, _ = _start(
        tmp_path,
        m4_universe.clean,
        "northstar_broadcast_master_v1",
        "run_m4_terminal_evidence_recovery",
    )
    ready = orchestrator.advance(model)
    assert ready.status is RunStatus.DELIVERY_READY
    evidence_root = tmp_path / "workspace" / "evidence"
    before = {
        path.name: path.read_bytes()
        for path in evidence_root.iterdir()
        if path.is_file()
    }
    (evidence_root / "ledger.json").unlink()

    restarted = HeadlessOrchestrator(
        workspace=tmp_path / "workspace",
        store_root=tmp_path / "store",
        run_id="run_m4_terminal_evidence_recovery",
    )
    assert restarted.advance(object()) == ready
    after = {
        path.name: path.read_bytes()
        for path in evidence_root.iterdir()
        if path.is_file()
    }
    assert after == before
    durable = restarted.load_run()
    assert len(durable.evidence) == 4
    assert all(
        hash_file(tmp_path / "workspace" / item.relative_path) == item.sha256
        for item in durable.evidence
    )


def test_foreign_corrupt_manifest_is_report_only_and_blocks(tmp_path: Path) -> None:
    universe = build_universe(tmp_path / "corrupt-universe")
    assert universe.clean.manifest is not None
    universe.clean.manifest.write_text("0" * 64 + " *wrong.mov\n", encoding="utf-8")
    orchestrator, model, _ = _start(
        tmp_path / "run",
        universe.clean,
        "northstar_broadcast_master_v1",
        "run_m4_corrupt_manifest",
    )
    run = orchestrator.load_run()
    keys = {item.requirement_id: item.measurement_key for item in run.requirements}
    checksum_finding = next(
        finding
        for finding in run.findings
        if keys[finding.requirement_id] == "package.checksums_match"
    )
    assert checksum_finding.options == ()

    blocked = orchestrator.advance(model)

    assert blocked.status is RunStatus.BLOCKED
    assert blocked.terminal_verdict is not None
    assert blocked.terminal_verdict.outcome is TerminalOutcome.BLOCKED


def test_corrupt_runtime_manifest_is_not_silently_refreshed_after_derivative(
    m4_universe,
    tmp_path: Path,
) -> None:
    orchestrator, model, _ = _start(
        tmp_path,
        m4_universe.hero,
        "northstar_broadcast_master_v1",
        "run_m4_corrupt_generated_manifest",
    )
    after_safe = orchestrator.advance(model)
    assert after_safe.status is RunStatus.FINDINGS_READY
    generated = next(
        item
        for item in orchestrator.load_run().current_assets()
        if item.asset.role is AssetRole.MANIFEST
    )
    assert generated.asset.created_by_action is not None
    manifest_path = tmp_path / "workspace" / generated.relative_path
    manifest_path.write_text("corrupt runtime manifest\n", encoding="utf-8")

    awaiting = orchestrator.advance(model)
    assert awaiting.status is RunStatus.AWAITING_HUMAN_DECISION
    after_derivative = orchestrator.resolve_decision(approved=True, actor="human:mu")
    assert after_derivative.status is RunStatus.FINDINGS_READY
    run = orchestrator.load_run()
    keys = {item.requirement_id: item.measurement_key for item in run.requirements}
    checksum = next(
        finding
        for finding in reversed(run.findings)
        if finding.status is FindingLifecycle.OPEN
        and keys[finding.requirement_id] == "package.checksums_match"
    )
    assert checksum.options == ()
    assert manifest_path.read_text(encoding="utf-8") == "corrupt runtime manifest\n"

    blocked = orchestrator.advance(model)
    assert blocked.status is RunStatus.BLOCKED
    assert blocked.terminal_verdict is not None



def test_stricter_report_only_disposition_cannot_gain_an_action_option(
    m4_universe,
    tmp_path: Path,
) -> None:
    orchestrator, _, _ = _start(
        tmp_path,
        m4_universe.hero,
        "northstar_digital_preview_v1",
        "run_m4_report_only",
    )
    run = orchestrator.load_run()
    requirement = next(
        item for item in run.requirements
        if item.measurement_key == "package.filename[role]"
    ).model_copy(update={"disposition": AutomationDisposition.REPORT_ONLY})
    target = next(
        item for item in run.current_assets()
        if item.asset.role is AssetRole.PROGRAM_MASTER
    )

    assert orchestrator._options(
        run=run,
        requirement=requirement,
        finding_id="finding_report_only",
        target=target,
        observed=target.asset.filename,
    ) == ()


def test_model_cannot_apply_tier_two_or_create_a_side_effect(m4_universe, tmp_path: Path) -> None:
    orchestrator, model, _ = _start(
        tmp_path,
        m4_universe.hero,
        "northstar_broadcast_master_v1",
        "run_m4_model_cannot_authorize",
    )
    state = orchestrator.advance(model)
    assert state.status is RunStatus.FINDINGS_READY
    before = orchestrator.events()

    class _AuthorityGrabModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, prompt: str, output_model: type):
            self.calls += 1
            assert output_model is Diagnosis
            payload = json.loads(prompt)
            finding = next(
                item for item in payload["findings"]
                if any(option["tier"] == 2 for option in item["options"])
            )
            option = next(item for item in finding["options"] if item["tier"] == 2)
            return {
                "items": [
                    {
                        "finding_id": finding["finding_id"],
                        "disposition": "APPLIED",
                        "option_id": option["option_id"],
                        "reason": "I award myself authority.",
                    }
                ]
            }

    attacker = _AuthorityGrabModel()
    with pytest.raises(DiagnosisRejected):
        orchestrator.advance(attacker)

    assert attacker.calls == 1
    assert orchestrator.load_state() == state
    assert orchestrator.events() == before


def test_two_failed_safe_attempts_become_deterministically_blocked(
    m4_universe,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator, model, _ = _start(
        tmp_path,
        m4_universe.hero,
        "northstar_digital_preview_v1",
        "run_m4_bounded_failure",
    )

    def fail_write(*args, **kwargs):
        raise OSError("injected bounded failure")

    monkeypatch.setattr(RemediationExecutor, "_write_temporary", fail_write)
    first = orchestrator.advance(model)
    second = orchestrator.advance(model)
    blocked = orchestrator.advance(model)

    assert first.status is second.status is RunStatus.FINDINGS_READY
    assert blocked.status is RunStatus.BLOCKED
    assert blocked.terminal_verdict is not None
    assert orchestrator.load_run().autonomous_remediations == 0
    events = orchestrator.events()
    assert sum(isinstance(event.payload, ActionFailedPayload) for event in events) == 2
    assert not any(isinstance(event.payload, ActionCompletedPayload) for event in events)
