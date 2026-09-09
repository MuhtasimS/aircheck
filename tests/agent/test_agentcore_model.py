"""M7 AgentCore semantic-model tests (hermetic; injected fake data-plane client).

These prove the M7 invariant "model agency, deterministic authority" at the S3
diagnosis seam: only the Diagnosis output is routed to AgentCore; a valid bound
recommendation is used; and ANY AgentCore fault or non-bindable recommendation
falls back visibly to the deterministic double so a misbehaving model can never
fail the run or cross the authority boundary. The runtime's diagnose_findings
remains the authoritative validator (tests/agent covers it separately).
"""

from __future__ import annotations

import json

import pytest

from aircheck.agent.agentcore_model import (
    AgentCoreSemanticModel,
    _diagnosis_bindable,
    _diagnosis_rejection,
)
from aircheck.agent.contracts import CandidateRequirementBatch
from aircheck.agent.diagnosis import Diagnosis


def _prompt(*, tier: int, option_id: str = "option_1", finding_id: str = "finding_1") -> str:
    return json.dumps(
        {
            "role": "AIRCheck S3",
            "prohibitions": ["no authority decisions"],
            "exposed_tools": ["read_finding", "create_normalized_audio_derivative"],
            "findings": [
                {
                    "finding_id": finding_id,
                    "observed": "-19.05 LUFS",
                    "expected": "between -26 and -22 LUFS",
                    "options": [
                        {
                            "option_id": option_id,
                            "tool": "create_normalized_audio_derivative",
                            "tier": tier,
                            "description": "Normalize a delivery derivative.",
                        }
                    ],
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


class _FakeFallback:
    """Deterministic double: picks a bound option per finding, bindably."""

    def __init__(self) -> None:
        self.calls: list[type] = []

    def generate(self, prompt: str, output_model: type):
        self.calls.append(output_model)
        if output_model is not Diagnosis:
            return {"marker": output_model.__name__}
        obj = json.loads(prompt)
        items = []
        for finding in obj["findings"]:
            options = finding["options"]
            tier_one = next((o for o in options if o["tier"] == 1), None)
            tier_two = next((o for o in options if o["tier"] == 2), None)
            if tier_one is not None:
                items.append({"finding_id": finding["finding_id"], "disposition": "APPLIED", "option_id": tier_one["option_id"], "reason": "fb"})
            elif tier_two is not None:
                items.append({"finding_id": finding["finding_id"], "disposition": "ESCALATE", "option_id": tier_two["option_id"], "reason": "fb"})
            else:
                items.append({"finding_id": finding["finding_id"], "disposition": "NO_ACTION", "option_id": None, "reason": "fb"})
        return {"items": items}


class _Body:
    def __init__(self, payload, *, raw: bytes | None = None) -> None:
        self._payload = payload
        self._raw = raw

    def read(self) -> bytes:
        if self._raw is not None:
            return self._raw
        return json.dumps(self._payload).encode("utf-8")


class _FakeAgentCore:
    def __init__(
        self, *, payload=None, raw: bytes | None = None, raise_exc: Exception | None = None
    ) -> None:
        self._payload = payload
        self._raw = raw
        self._raise = raise_exc
        self.invocations: list[dict] = []

    def invoke_agent_runtime(self, **kwargs):
        self.invocations.append(kwargs)
        if self._raise is not None:
            raise self._raise
        return {"response": _Body(self._payload, raw=self._raw)}


def _model(fake_client, fallback=None):
    return AgentCoreSemanticModel(
        fallback=fallback or _FakeFallback(),
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:0:runtime/Diag-abc",
        region="us-east-1",
        model_id="us.amazon.nova-lite-v1:0",
        client=fake_client,
    )


def test_non_diagnosis_output_never_touches_agentcore() -> None:
    client = _FakeAgentCore(payload={"should": "not be used"})
    fb = _FakeFallback()
    model = _model(client, fb)
    result = model.generate("{}", CandidateRequirementBatch)
    assert result == {"marker": "CandidateRequirementBatch"}
    assert client.invocations == []  # S1/S2 never cross the AgentCore boundary
    assert fb.calls == [CandidateRequirementBatch]


def test_valid_bound_recommendation_is_used() -> None:
    prompt = _prompt(tier=2, option_id="option_1")
    payload = {"diagnosis": {"items": [{"finding_id": "finding_1", "disposition": "ESCALATE", "option_id": "option_1", "reason": "loudness needs human authority"}]}}
    client = _FakeAgentCore(payload=payload)
    model = _model(client)
    result = model.generate(prompt, Diagnosis)
    # The runtime can validate it: it is a valid Diagnosis referencing the bound option.
    Diagnosis.model_validate(result)
    assert result["items"][0]["disposition"] == "ESCALATE"
    assert len(client.invocations) == 1
    # runtimeSessionId must satisfy the >=33-char AgentCore contract.
    assert len(client.invocations[0]["runtimeSessionId"]) >= 33
    assert model.invocations[-1].source == "AGENTCORE"


def test_agentcore_transport_error_falls_back_visibly() -> None:
    client = _FakeAgentCore(raise_exc=RuntimeError("throttled"))
    fb = _FakeFallback()
    model = _model(client, fb)
    result = model.generate(_prompt(tier=2), Diagnosis)
    Diagnosis.model_validate(result)  # fallback produced a valid diagnosis
    assert model.invocations[-1].source == "FALLBACK"
    assert model.invocations[-1].fallback_reason == "RuntimeError"


def test_unbindable_recommendation_is_overridden_by_fallback() -> None:
    # AgentCore returns a schema-valid Diagnosis that references an option that is
    # NOT exposed for this finding -> it must not reach the runtime; fall back.
    prompt = _prompt(tier=2, option_id="option_1")
    payload = {"items": [{"finding_id": "finding_1", "disposition": "APPLIED", "option_id": "option_ghost", "reason": "hallucinated"}]}
    client = _FakeAgentCore(payload=payload)
    fb = _FakeFallback()
    model = _model(client, fb)
    result = model.generate(prompt, Diagnosis)
    Diagnosis.model_validate(result)
    assert result["items"][0]["option_id"] == "option_1"  # the bound fallback choice
    assert model.invocations[-1].source == "FALLBACK"
    assert model.invocations[-1].fallback_reason == "unbindable_recommendation"


def test_wrong_tier_recommendation_is_overridden() -> None:
    # ESCALATE must reference a Tier-2 option; a Tier-1-only prompt with an
    # ESCALATE recommendation is not bindable -> fall back.
    prompt = _prompt(tier=1, option_id="option_1")
    payload = {"items": [{"finding_id": "finding_1", "disposition": "ESCALATE", "option_id": "option_1", "reason": "wrong tier"}]}
    model = _model(_FakeAgentCore(payload=payload))
    result = model.generate(prompt, Diagnosis)
    assert result["items"][0]["disposition"] == "APPLIED"  # fallback's bound choice
    assert model.invocations[-1].source == "FALLBACK"


def test_diagnosis_bindable_helper_matches_runtime_rules() -> None:
    prompt_obj = json.loads(_prompt(tier=2, option_id="option_1"))
    good = {"items": [{"finding_id": "finding_1", "disposition": "ESCALATE", "option_id": "option_1", "reason": "ok"}]}
    assert _diagnosis_bindable(good, prompt_obj) is True
    # Missing a finding, duplicate, wrong option, wrong tier, bad NO_ACTION shape.
    assert _diagnosis_bindable({"items": []}, prompt_obj) is False
    assert _diagnosis_bindable({"items": [{"finding_id": "finding_1", "disposition": "APPLIED", "option_id": "option_1", "reason": "x"}]}, prompt_obj) is False
    assert _diagnosis_bindable({"items": [{"finding_id": "finding_1", "disposition": "NO_ACTION", "option_id": "option_1", "reason": "x"}]}, prompt_obj) is False
    assert _diagnosis_bindable({"items": [{"finding_id": "other", "disposition": "ESCALATE", "option_id": "option_1", "reason": "x"}]}, prompt_obj) is False


# --------------------------------------------------------------------------- M8 P3
# Malformed AgentCore output must produce a VISIBLE deterministic fallback, never an
# uncaught adapter exception or a stranded run. These are the exact audit shapes: any
# response the runtime's typed Diagnosis model would later reject must be caught inside
# the adapter's protected boundary and overridden by the double.


def _item(**over):
    base = {"finding_id": "finding_1", "disposition": "ESCALATE", "option_id": "option_1", "reason": "ok"}
    base.update(over)
    return base


# Each payload is what AgentCore "returns"; every one must reject -> fallback.
_MALFORMED = {
    "empty_reason": {"diagnosis": {"items": [_item(reason="")]}},
    "whitespace_reason": {"diagnosis": {"items": [_item(reason="   ")]}},
    "missing_reason": {"diagnosis": {"items": [{"finding_id": "finding_1", "disposition": "ESCALATE", "option_id": "option_1"}]}},
    "extra_field": {"diagnosis": {"items": [_item(unexpected="x")]}},
    "wrong_disposition_type": {"diagnosis": {"items": [_item(disposition=2)]}},
    "wrong_option_type": {"diagnosis": {"items": [_item(option_id=5)]}},
    "bad_finding_id_pattern": {"diagnosis": {"items": [_item(finding_id="Finding_1")]}},
    "items_list_of_scalars": {"diagnosis": {"items": [1, 2]}},
    "items_is_a_dict": {"diagnosis": {"items": {"finding_1": "ESCALATE"}}},
    "items_missing": {"diagnosis": {"not_items": []}},
    "error_envelope": {"error": "ValidationException"},
    "empty_object": {},
}


@pytest.mark.parametrize("name", sorted(_MALFORMED))
def test_malformed_agentcore_output_falls_back_visibly(name) -> None:
    prompt = _prompt(tier=2, option_id="option_1")
    fb = _FakeFallback()
    model = _model(_FakeAgentCore(payload=_MALFORMED[name]), fb)
    # Must not raise, and must yield a valid, runtime-acceptable diagnosis.
    result = model.generate(prompt, Diagnosis)
    Diagnosis.model_validate(result)
    assert result["items"][0]["option_id"] == "option_1"  # the bound fallback choice
    inv = model.invocations[-1]
    assert inv.source == "FALLBACK"
    assert inv.fallback_reason  # a truthful non-empty reason
    assert fb.calls[-1] is Diagnosis  # the double actually produced the result


def test_non_json_body_falls_back_visibly() -> None:
    prompt = _prompt(tier=2, option_id="option_1")
    model = _model(_FakeAgentCore(raw=b"this is not json {"))
    result = model.generate(prompt, Diagnosis)
    Diagnosis.model_validate(result)
    assert model.invocations[-1].source == "FALLBACK"


def test_malformed_output_never_classified_agentcore() -> None:
    # No malformed shape may be logged as a successful AGENTCORE invocation.
    prompt = _prompt(tier=2, option_id="option_1")
    for payload in _MALFORMED.values():
        model = _model(_FakeAgentCore(payload=payload))
        model.generate(prompt, Diagnosis)
        assert model.invocations[-1].source == "FALLBACK"


def test_rejection_reasons_are_truthful() -> None:
    prompt_obj = json.loads(_prompt(tier=2, option_id="option_1"))
    assert _diagnosis_rejection({"error": "x"}, prompt_obj) == "agent_error_envelope"
    assert _diagnosis_rejection({"diagnosis": {"items": [_item(reason="")]}}, prompt_obj) == "malformed_diagnosis"
    assert _diagnosis_rejection({"diagnosis": {"items": [1]}}, prompt_obj) == "malformed_diagnosis"
    # A valid, bound recommendation is accepted (None).
    good = {"diagnosis": {"items": [_item()]}}
    assert _diagnosis_rejection(good, prompt_obj) is None
