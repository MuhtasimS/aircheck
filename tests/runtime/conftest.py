"""Shared M4 synthetic universe and semantic-boundary fixtures."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from aircheck.agent.diagnosis import Diagnosis
from aircheck.agent.contracts import CandidateRequirementBatch
from aircheck.domain.planning import PlanProposal
from synthetic.generator.last_lightkeeper import build_universe
from tests.agent.fixtures import northstar_batch


@pytest.fixture(scope="session")
def m4_universe(tmp_path_factory: pytest.TempPathFactory):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("M4 media proof requires ffmpeg and ffprobe")
    return build_universe(tmp_path_factory.mktemp("m4-universe") / "universe")


class HeroSemanticModel:
    """Typed deterministic double; authority and correctness stay in runtime code."""

    def __init__(self, *, profile_id: str, segments) -> None:
        self._profile_id = profile_id
        self._segments = segments
        self.calls: list[type] = []
        self.prompts: list[tuple[type, str]] = []

    def generate(self, prompt: str, output_model: type):
        self.calls.append(output_model)
        self.prompts.append((output_model, prompt))
        if output_model is CandidateRequirementBatch:
            return northstar_batch(self._profile_id, self._segments)
        if output_model is PlanProposal:
            return {"ordered_items": []}
        if output_model is Diagnosis:
            payload = json.loads(prompt)
            items = []
            for finding in payload["findings"]:
                options = finding["options"]
                tier_one = next((item for item in options if item["tier"] == 1), None)
                tier_two = next((item for item in options if item["tier"] == 2), None)
                if tier_one is not None:
                    disposition = "APPLIED"
                    option_id = tier_one["option_id"]
                elif tier_two is not None:
                    disposition = "ESCALATE"
                    option_id = tier_two["option_id"]
                else:
                    disposition = "NO_ACTION"
                    option_id = None
                items.append(
                    {
                        "finding_id": finding["finding_id"],
                        "disposition": disposition,
                        "option_id": option_id,
                        "reason": "Bound M4 scripted diagnosis.",
                    }
                )
            return {"items": items}
        raise AssertionError(output_model)
