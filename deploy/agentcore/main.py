"""AIRCheck S3 diagnosis agent — Strands on Amazon Bedrock AgentCore Runtime.

Model agency, deterministic authority. This agent RECOMMENDS a disposition among
the already-authorized, typed remediation OPTIONS that AIRCheck exposes at the
bounded S3 diagnosis seam. AIRCheck's deterministic runtime re-validates every
recommendation (aircheck.agent.diagnosis.diagnose_findings) and owns all authority
and terminal truth; the agent never sees a filesystem path, never mints authority,
and never decides correctness, PASS/FAIL, measurements, or delivery readiness. It
returns typed JSON only, which the AIRCheck API treats as UNTRUSTED and validates
before use (falling back to a deterministic double on any fault). This is the one
point where model agency legitimately belongs.
"""

from __future__ import annotations

import json
import os
import re
from typing import Literal, Optional

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import BaseModel, Field
from strands import Agent
from strands.models import BedrockModel

app = BedrockAgentCoreApp()
log = app.logger

DEFAULT_MODEL = os.environ.get("AIRCHECK_AGENTCORE_MODEL_ID", "us.amazon.nova-lite-v1:0")

SYSTEM = (
    "You are AIRCheck S3, a bounded diagnosis step inside a deterministic "
    "media-quality-control pipeline. You receive typed findings; each finding "
    "lists a small set of ALREADY-AUTHORIZED remediation OPTIONS, each with an "
    "option_id and a tier (1 = safe reversible working-copy edit; 2 = "
    "content-affecting derivative that requires explicit human authority). For "
    "EVERY finding, return exactly one item with one disposition: APPLIED with the "
    "option_id of a tier-1 option; ESCALATE with the option_id of a tier-2 option; "
    "or NO_ACTION with option_id null. CRITICAL: if a finding's options list is "
    "EMPTY, you MUST return NO_ACTION with option_id null — never ESCALATE or "
    "APPLIED, because there is no option to reference. ESCALATE requires a listed "
    "tier-2 option and APPLIED requires a listed tier-1 option; copy the option_id "
    "EXACTLY from that finding's listed options. Diagnose every finding exactly once. "
    "You never decide correctness, authority, measurements, or delivery readiness — "
    "only which already-authorized option to recommend, with a short reason. Output "
    "typed JSON only."
)


class DiagnosisItem(BaseModel):
    finding_id: str
    disposition: Literal["APPLIED", "ESCALATE", "NO_ACTION"]
    option_id: Optional[str] = None
    reason: str


class Diagnosis(BaseModel):
    items: list[DiagnosisItem] = Field(min_length=1)


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in model output")
    return text[start : end + 1]


def _diagnose(prompt: str, model_id: str) -> dict:
    model = BedrockModel(model_id=model_id)
    agent = Agent(model=model, system_prompt=SYSTEM, callback_handler=None)
    # Primary: Strands typed structured output.
    try:
        result = agent.structured_output(Diagnosis, prompt)
        return result.model_dump()
    except (AttributeError, TypeError):
        pass
    # Fallback for strands API variation: plain call + strict JSON validation.
    response = agent(
        prompt + '\n\nReturn ONLY a JSON object of the form '
        '{"items":[{"finding_id":...,"disposition":...,"option_id":...,"reason":...}]}'
    )
    text = getattr(response, "message", None)
    if isinstance(text, dict):
        text = text.get("content", [{}])[0].get("text", "")
    else:
        text = str(response)
    obj = json.loads(_extract_json(text))
    return Diagnosis.model_validate(obj).model_dump()


@app.entrypoint
def invoke(payload):
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    model_id = (payload.get("model_id") if isinstance(payload, dict) else None) or DEFAULT_MODEL
    if not prompt:
        return {"error": "missing_prompt"}
    try:
        diagnosis = _diagnose(prompt, model_id)
        log.info("aircheck.agentcore.diagnosed items=%d model=%s", len(diagnosis.get("items", [])), model_id)
        return {"diagnosis": diagnosis, "model_id": model_id, "source": "agentcore"}
    except Exception as exc:  # product-safe: no payloads or reasoning leaked
        log.error("aircheck.agentcore.error %s", type(exc).__name__)
        return {"error": type(exc).__name__}


if __name__ == "__main__":
    app.run()
