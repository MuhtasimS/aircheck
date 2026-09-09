"""M8 durable AgentCore provenance tests (audit P4).

CloudWatch proves AgentCore participation for a run; these prove the product's own
DURABLE evidence preserves it too, so a judge can distinguish a genuine AgentCore
recommendation from a deterministic fallback from the run's evidence alone. The
provenance is non-authority-bearing: it never affects measurements, predicate
truth, authorization, or the terminal outcome, and it carries no chain-of-thought,
prompt, or credential.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from aircheck.agent.agentcore_model import AgentCoreInvocation
from apps.api.models import CreateRunRequest
from apps.api.service import AGENTCORE_PROVENANCE_FILE, AIRCheckService
from synthetic.generator.last_lightkeeper import build_universe

BROADCAST = "northstar_broadcast_master_v1"


def _svc(tmp_path, monkeypatch, **kw) -> AIRCheckService:
    for var in ("AIRCHECK_S3_BUCKET", "AIRCHECK_STORE_ROOT", "AIRCHECK_WORKSPACE_ROOT",
                "AIRCHECK_UNIVERSE_ROOT", "AIRCHECK_AGENTCORE_RUNTIME_ARN"):
        monkeypatch.delenv(var, raising=False)
    return AIRCheckService(
        store_root=tmp_path / "store",
        workspace_root=tmp_path / "workspace",
        universe_root=tmp_path / "universe",
        **kw,
    )


def _invocation(source: str, session: str, reason: str | None = None) -> AgentCoreInvocation:
    return AgentCoreInvocation(
        session_id=session,
        source=source,
        model_id="us.amazon.nova-lite-v1:0",
        runtime_arn_tail="aircheck_diagnosis-ABC",
        latency_ms=1234,
        finding_count=1,
        dispositions=("ESCALATE",),
        fallback_reason=reason,
    )


class _Model:
    def __init__(self, invocations) -> None:
        self.invocations = list(invocations)


def test_record_and_load_provenance_classifies_and_merges(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    run_id = "run_x"
    (svc.workspace_root / run_id).mkdir(parents=True)

    # First phase: an AGENTCORE invocation plus a FALLBACK invocation.
    svc._record_provenance(run_id, _Model([
        _invocation("AGENTCORE", "s1"),
        _invocation("FALLBACK", "s2", reason="unbindable_recommendation"),
    ]))
    # Second phase (post-approval): a new invocation, plus a duplicate session id.
    svc._record_provenance(run_id, _Model([
        _invocation("AGENTCORE", "s1"),  # duplicate -> ignored
        _invocation("AGENTCORE", "s3"),
    ]))

    prov = svc._load_provenance(run_id)
    assert prov is not None
    # Any AgentCore participation makes the run's semantic source AGENTCORE.
    assert prov.semantic_source == "AGENTCORE"
    assert prov.model_id == "us.amazon.nova-lite-v1:0"
    # Sessions merged, duplicate de-duplicated.
    assert sorted(i.session_id for i in prov.invocations) == ["s1", "s2", "s3"]
    fb = [i for i in prov.invocations if i.source == "FALLBACK"]
    assert fb and fb[0].fallback_reason == "unbindable_recommendation"


def test_all_fallback_reports_fallback_source(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    run_id = "run_fb"
    (svc.workspace_root / run_id).mkdir(parents=True)
    svc._record_provenance(run_id, _Model([
        _invocation("FALLBACK", "s1", reason="RuntimeError"),
    ]))
    prov = svc._load_provenance(run_id)
    assert prov.semantic_source == "FALLBACK"


def test_double_only_writes_no_provenance(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    run_id = "run_d"
    (svc.workspace_root / run_id).mkdir(parents=True)

    class _Double:  # the deterministic double emits no invocation records
        pass

    svc._record_provenance(run_id, _Double())
    assert not (svc.workspace_root / run_id / AGENTCORE_PROVENANCE_FILE).exists()
    assert svc._load_provenance(run_id) is None


def test_provenance_sidecar_has_no_sensitive_content(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    run_id = "run_s"
    (svc.workspace_root / run_id).mkdir(parents=True)
    svc._record_provenance(run_id, _Model([_invocation("AGENTCORE", "s1")]))
    text = (svc.workspace_root / run_id / AGENTCORE_PROVENANCE_FILE).read_text(encoding="utf-8")
    lowered = text.lower()
    for forbidden in ("prompt", "reasoning", "chain", "secret", "token", "credential", "arn:aws"):
        assert forbidden not in lowered


# --------------------------------------------------------------- end-to-end (P4)


class _Body:
    def __init__(self, payload) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _ReplayAgentCore:
    """A fake AgentCore data plane that returns a valid, bindable recommendation.

    It reads the exposed options from the prompt and recommends deterministically
    (APPLIED for a tier-1 option, ESCALATE for a tier-2 option, else NO_ACTION),
    exactly as the real bounded agent would, so the run reaches the canonical hero
    states and every invocation classifies as AGENTCORE.
    """

    def __init__(self) -> None:
        self.calls = 0

    def invoke_agent_runtime(self, **kwargs):
        self.calls += 1
        payload = json.loads(kwargs["payload"])
        prompt = json.loads(payload["prompt"])
        items = []
        for finding in prompt["findings"]:
            options = finding.get("options", [])
            t1 = next((o for o in options if o["tier"] == 1), None)
            t2 = next((o for o in options if o["tier"] == 2), None)
            if t1 is not None:
                items.append({"finding_id": finding["finding_id"], "disposition": "APPLIED",
                              "option_id": t1["option_id"], "reason": "agentcore replay tier-1"})
            elif t2 is not None:
                items.append({"finding_id": finding["finding_id"], "disposition": "ESCALATE",
                              "option_id": t2["option_id"], "reason": "agentcore replay tier-2"})
            else:
                items.append({"finding_id": finding["finding_id"], "disposition": "NO_ACTION",
                              "option_id": None, "reason": "agentcore replay no-op"})
        body = {"diagnosis": {"items": items}, "model_id": "us.amazon.nova-lite-v1:0", "source": "agentcore"}
        return {"response": _Body(body)}


@pytest.fixture(scope="module")
def universe_dir(tmp_path_factory) -> Path:
    dest = tmp_path_factory.mktemp("prov_universe") / "u"
    build_universe(dest)
    return dest


def test_agentcore_provenance_is_visible_in_durable_evidence(tmp_path, monkeypatch, universe_dir):
    monkeypatch.setenv("AIRCHECK_AGENTCORE_RUNTIME_ARN",
                       "arn:aws:bedrock-agentcore:us-east-1:0:runtime/aircheck_diagnosis-ABC")
    monkeypatch.setenv("AIRCHECK_AGENTCORE_MODEL_ID", "us.amazon.nova-lite-v1:0")
    for var in ("AIRCHECK_S3_BUCKET", "AIRCHECK_STORE_ROOT", "AIRCHECK_WORKSPACE_ROOT",
                "AIRCHECK_UNIVERSE_ROOT"):
        monkeypatch.delenv(var, raising=False)

    replay = _ReplayAgentCore()
    svc = AIRCheckService(
        store_root=tmp_path / "store",
        workspace_root=tmp_path / "workspace",
        universe_root=universe_dir,
        agentcore_client=replay,
    )
    assert svc.agentcore_enabled

    created = svc.create_run(CreateRunRequest(profile_id=BROADCAST, package_type="hero"))
    run_id = created.run_id
    svc.submit_decision(run_id, approved=True)

    evidence = svc.get_evidence(run_id)
    prov = evidence.agentcore_provenance
    assert prov is not None
    assert prov.semantic_source == "AGENTCORE"
    assert prov.model_id == "us.amazon.nova-lite-v1:0"
    # The real AgentCore path was exercised (create Tier-1 + Tier-2 escalate, plus
    # post-approval re-diagnosis), every invocation classified AGENTCORE.
    assert len(prov.invocations) >= 2
    assert {i.source for i in prov.invocations} == {"AGENTCORE"}
    assert any("ESCALATE" in i.dispositions for i in prov.invocations)
    assert replay.calls >= 2
