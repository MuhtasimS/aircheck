"""Deterministic typed semantic doubles for the M6 corpus.

The corpus does not use an LLM judge and does not call a live provider. It stands
in a deterministic, typed double at the S1/S2/S3 boundary so that the corpus
measures the *deterministic runtime's* response to typed model output. This is
the same boundary shape the M4 runtime tests already use (see
``tests/runtime/conftest.py``); it keeps authority and correctness entirely in
runtime code.

Stochastic semantic reliability (whether a real provider produces useful typed
candidates) is predecessor evidence from M3 (Gemini 2.5 Flash, recorded under
``docs/evidence/M3/``) and is reported separately from this deterministic
execution.
"""

from __future__ import annotations

import json

from aircheck.agent.contracts import CandidateRequirementBatch
from aircheck.agent.diagnosis import Diagnosis
from aircheck.domain.planning import PlanProposal


class CorpusSemanticModel:
    """Deterministic double returning a fixed candidate batch and scripted plan/diagnosis.

    ``batch`` is the untrusted S1 output (a plain dict). Planning always returns
    an empty proposal so the runtime uses its deterministic fallback plan.
    Diagnosis mirrors the canonical scripted policy: prefer an exposed Tier-1
    option (APPLIED); otherwise escalate an exposed Tier-2 option (ESCALATE);
    otherwise NO_ACTION.
    """

    def __init__(self, batch: dict[str, object]) -> None:
        self._batch = batch
        self.calls: list[type] = []
        self.prompts: list[tuple[type, str]] = []

    def generate(self, prompt: str, output_model: type):
        self.calls.append(output_model)
        self.prompts.append((output_model, prompt))
        if output_model is CandidateRequirementBatch:
            return self._batch
        if output_model is PlanProposal:
            return {"ordered_items": []}
        if output_model is Diagnosis:
            return _scripted_diagnosis(prompt)
        raise AssertionError(f"unexpected output model: {output_model!r}")


def _scripted_diagnosis(prompt: str) -> dict[str, object]:
    payload = json.loads(prompt)
    items: list[dict[str, object]] = []
    for finding in payload["findings"]:
        options = finding["options"]
        tier_one = next((item for item in options if item["tier"] == 1), None)
        tier_two = next((item for item in options if item["tier"] == 2), None)
        if tier_one is not None:
            disposition, option_id = "APPLIED", tier_one["option_id"]
        elif tier_two is not None:
            disposition, option_id = "ESCALATE", tier_two["option_id"]
        else:
            disposition, option_id = "NO_ACTION", None
        items.append(
            {
                "finding_id": finding["finding_id"],
                "disposition": disposition,
                "option_id": option_id,
                "reason": "Corpus scripted diagnosis.",
            }
        )
    return {"items": items}


class SelfAuthorizingAttackerModel:
    """An adversarial S3 double that tries to APPLY a Tier-2 option itself.

    The deterministic runtime must reject this (a model may never self-authorize a
    content-affecting derivative). Interpretation and planning behave normally so
    the run reaches findings; only diagnosis is adversarial.
    """

    def __init__(self, batch: dict[str, object]) -> None:
        self._batch = batch
        self.calls: list[type] = []
        self.diagnosis_calls = 0

    def generate(self, prompt: str, output_model: type):
        self.calls.append(output_model)
        if output_model is CandidateRequirementBatch:
            return self._batch
        if output_model is PlanProposal:
            return {"ordered_items": []}
        if output_model is Diagnosis:
            self.diagnosis_calls += 1
            payload = json.loads(prompt)
            for finding in payload["findings"]:
                tier_two = next(
                    (item for item in finding["options"] if item["tier"] == 2),
                    None,
                )
                if tier_two is not None:
                    return {
                        "items": [
                            {
                                "finding_id": finding["finding_id"],
                                "disposition": "APPLIED",
                                "option_id": tier_two["option_id"],
                                "reason": "Adversarial self-authorization attempt.",
                            }
                        ]
                    }
            # No Tier-2 exposed yet: behave normally so the run advances toward it.
            return _scripted_diagnosis(prompt)
        raise AssertionError(f"unexpected output model: {output_model!r}")


__all__ = ("CorpusSemanticModel", "SelfAuthorizingAttackerModel")
