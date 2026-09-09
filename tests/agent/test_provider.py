"""Boundary tests for the exact M3a-selected semantic provider adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class _FakeAgent:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.result


def test_gemini_adapter_uses_frozen_configuration_and_returns_only_structured_output() -> None:
    from aircheck.agent.contracts import CandidateRequirementBatch
    from aircheck.agent.provider import Gemini25FlashModel

    captured = {}
    fake_agent = _FakeAgent(SimpleNamespace(structured_output={"requirements": []}))

    def model_factory(**kwargs):
        captured["model"] = kwargs
        return "model"

    def agent_factory(**kwargs):
        captured["agent"] = kwargs
        return fake_agent

    provider = Gemini25FlashModel(
        project="redacted-project",
        location="us-central1",
        model_factory=model_factory,
        agent_factory=agent_factory,
    )
    output = provider.generate("bounded prompt", CandidateRequirementBatch)

    assert output == {"requirements": []}
    assert captured["model"] == {
        "client_args": {
            "vertexai": True,
            "project": "redacted-project",
            "location": "us-central1",
        },
        "model_id": "gemini-2.5-flash",
        "params": {"temperature": 0, "max_output_tokens": 8192},
    }
    assert captured["agent"]["model"] == "model"
    assert captured["agent"]["tools"] == []
    assert fake_agent.calls == [
        (
            "bounded prompt",
            {
                "structured_output_model": CandidateRequirementBatch,
                "limits": {"turns": 4, "output_tokens": 12000, "total_tokens": 24000},
            },
        )
    ]


def test_gemini_adapter_refuses_missing_structured_output() -> None:
    from aircheck.agent.contracts import CandidateRequirementBatch
    from aircheck.agent.provider import Gemini25FlashModel, ProviderOutputError

    provider = Gemini25FlashModel(
        project="redacted-project",
        agent_factory=lambda **_: _FakeAgent(SimpleNamespace(structured_output=None)),
        model_factory=lambda **_: "model",
    )

    with pytest.raises(ProviderOutputError, match="structured output"):
        provider.generate("bounded prompt", CandidateRequirementBatch)
