"""AgentCore-backed semantic model for the bounded S3 diagnosis step (M7).

Model agency, deterministic authority. This model only *recommends* among the
already-bound typed remediation options the runtime exposes at the S3 diagnosis
seam (``aircheck.agent.diagnosis``). It routes ONLY the ``Diagnosis`` output to a
Strands agent hosted on Amazon Bedrock AgentCore Runtime; S1 interpretation and
S2 plan proposal stay on the injected deterministic double. The AgentCore output
is UNTRUSTED — ``diagnose_findings`` re-validates every finding/option/tier
binding, so a wrong or malformed recommendation can never cross the authority
boundary. As a second guard so a misbehaving model can never *fail the run*
instead of being overridden, this model pre-checks the AgentCore recommendation
against the exact options exposed in the prompt and falls back to the
deterministic double whenever the recommendation is not perfectly bindable, or
on any AgentCore configuration/transport/throttle/parse error. Every invocation
is recorded as a product-safe telemetry line (session id, runtime, model, latency,
source, per-finding dispositions — never model chain-of-thought or payloads) and
emitted to stdout for CloudWatch. The deterministic runtime remains the sole
owner of measurements, predicates, authority, side effects, evidence, and
terminal verdicts.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from aircheck.agent.diagnosis import Diagnosis, DiagnosisDisposition


@dataclass
class AgentCoreInvocation:
    """Product-safe record of one S3-diagnosis semantic invocation."""

    session_id: str
    source: str  # "AGENTCORE" | "FALLBACK"
    model_id: str
    runtime_arn_tail: str
    latency_ms: int
    finding_count: int
    dispositions: tuple[str, ...] = field(default_factory=tuple)
    fallback_reason: str | None = None

    def as_log(self) -> dict[str, Any]:
        return {
            "event": "aircheck.agentcore.invocation",
            "session_id": self.session_id,
            "source": self.source,
            "model_id": self.model_id,
            "runtime": self.runtime_arn_tail,
            "latency_ms": self.latency_ms,
            "findings": self.finding_count,
            "dispositions": list(self.dispositions),
            "fallback_reason": self.fallback_reason,
        }


def _extract_diagnosis(data: Any) -> Any:
    """Unwrap the agent envelope to the raw diagnosis object, or None if absent.

    Never raises: a shape this cannot turn into a candidate diagnosis object
    returns None so the caller falls back visibly.
    """
    diag = data.get("diagnosis", data) if isinstance(data, dict) else data
    if isinstance(diag, str):
        try:
            diag = json.loads(diag)
        except (TypeError, ValueError):
            return None
    return diag if isinstance(diag, dict) else None


def _is_error_envelope(data: Any) -> bool:
    """True for the deployed agent's product-safe ``{"error": ...}`` failure reply."""
    return isinstance(data, dict) and "error" in data and "diagnosis" not in data


def _is_bound(diagnosis: Diagnosis, prompt_obj: dict[str, Any]) -> bool:
    """Mirror ``diagnose_findings`` binding rules on an already-typed Diagnosis.

    Runs only on a value that has already passed the canonical ``Diagnosis`` model,
    so it inspects typed fields. Guarantees the runtime's authoritative validator
    will accept the recommendation; otherwise the caller falls back to the double.
    """
    findings = {
        f.get("finding_id"): f
        for f in prompt_obj.get("findings", [])
        if isinstance(f, dict)
    }
    received = [item.finding_id for item in diagnosis.items]
    if len(received) != len(set(received)) or set(received) != set(findings):
        return False
    for item in diagnosis.items:
        finding = findings[item.finding_id]
        options = {
            o.get("option_id"): o
            for o in finding.get("options", [])
            if isinstance(o, dict)
        }
        if item.disposition is DiagnosisDisposition.NO_ACTION:
            if item.option_id is not None:
                return False
            continue
        option = options.get(item.option_id)
        if option is None:
            return False
        if item.disposition is DiagnosisDisposition.APPLIED and option.get("tier") != 1:
            return False
        if item.disposition is DiagnosisDisposition.ESCALATE and option.get("tier") != 2:
            return False
    return True


def _diagnosis_rejection(data: Any, prompt_obj: dict[str, Any]) -> str | None:
    """Authoritative accept/reject for untrusted AgentCore output.

    Returns None to ACCEPT, or a short truthful reason to REJECT (fall back). It
    validates against the canonical typed ``Diagnosis`` model INSIDE this protected
    boundary — the exact model the runtime will later enforce — so empty/whitespace
    reasons, missing/extra fields, wrong shapes/types, unbindable recommendations,
    and error envelopes all become a visible deterministic fallback rather than an
    uncaught adapter exception or a stranded run. Never raises.
    """
    try:
        if _is_error_envelope(data):
            return "agent_error_envelope"
        diag = _extract_diagnosis(data)
        if diag is None:
            return "no_diagnosis_object"
        try:
            parsed = Diagnosis.model_validate(diag)
        except Exception:
            # empty/whitespace reason, missing/extra field, wrong type/shape, bad id
            return "malformed_diagnosis"
        if not _is_bound(parsed, prompt_obj):
            return "unbindable_recommendation"
        return None
    except Exception:
        # Defensive: nothing about untrusted output may escape as an exception.
        return "adapter_validation_error"


def _diagnosis_bindable(data: Any, prompt_obj: dict[str, Any]) -> bool:
    """Back-compat boolean form of the authoritative acceptance check."""
    return _diagnosis_rejection(data, prompt_obj) is None


class AgentCoreSemanticModel:
    """Composite semantic model: AgentCore for S3 diagnosis, double otherwise."""

    def __init__(
        self,
        *,
        fallback: Any,
        runtime_arn: str,
        region: str,
        model_id: str,
        qualifier: str = "DEFAULT",
        client: Any = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._fallback = fallback
        self._runtime_arn = runtime_arn
        self._region = region
        self._model_id = model_id
        self._qualifier = qualifier
        self._client = client
        self._timeout_s = timeout_s
        self.invocations: list[AgentCoreInvocation] = []

    @property
    def _agentcore(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "bedrock-agentcore",
                region_name=self._region,
                config=Config(
                    read_timeout=self._timeout_s,
                    connect_timeout=10,
                    retries={"max_attempts": 2, "mode": "standard"},
                ),
            )
        return self._client

    def _emit(self, record: AgentCoreInvocation) -> None:
        self.invocations.append(record)
        try:
            sys.stdout.write(json.dumps(record.as_log(), separators=(",", ":")) + "\n")
            sys.stdout.flush()
        except Exception:
            pass

    def generate(self, prompt: str, output_model: type) -> Any:
        # Only the S3 diagnosis crosses the AgentCore boundary; S1/S2 stay local.
        if output_model is not Diagnosis:
            return self._fallback.generate(prompt, output_model)

        try:
            prompt_obj = json.loads(prompt)
        except (TypeError, ValueError):
            prompt_obj = {"findings": []}
        finding_count = len(prompt_obj.get("findings", []))
        # runtimeSessionId must be >= 33 chars (AgentCore contract).
        session_id = uuid.uuid4().hex + uuid.uuid4().hex
        arn_tail = self._runtime_arn.rsplit("/", 1)[-1] if self._runtime_arn else "n/a"
        started = time.time()

        def fallback(reason: str) -> Any:
            result = self._fallback.generate(prompt, output_model)
            items = result.get("items", []) if isinstance(result, dict) else []
            self._emit(
                AgentCoreInvocation(
                    session_id=session_id,
                    source="FALLBACK",
                    model_id=self._model_id,
                    runtime_arn_tail=arn_tail,
                    latency_ms=int((time.time() - started) * 1000),
                    finding_count=finding_count,
                    dispositions=tuple(i.get("disposition", "?") for i in items),
                    fallback_reason=reason,
                )
            )
            return result

        try:
            resp = self._agentcore.invoke_agent_runtime(
                agentRuntimeArn=self._runtime_arn,
                runtimeSessionId=session_id,
                qualifier=self._qualifier,
                contentType="application/json",
                accept="application/json",
                payload=json.dumps(
                    {"prompt": prompt, "model_id": self._model_id}
                ).encode("utf-8"),
            )
            raw = resp["response"].read()
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
        except Exception as exc:  # config/transport/throttle/parse — visible fallback
            return fallback(type(exc).__name__)

        # Authoritative validation inside the protected boundary: any malformed or
        # unbindable output becomes a truthful deterministic fallback, never an
        # uncaught adapter exception or a stranded run (M8 audit P3). The canonical
        # typed Diagnosis model — the exact one the runtime enforces — is applied
        # here, so empty/whitespace reasons, missing/extra fields, wrong shapes, and
        # error envelopes all fall back visibly.
        rejection = _diagnosis_rejection(data, prompt_obj)
        if rejection is not None:
            return fallback(rejection)

        diag = _extract_diagnosis(data)
        items = diag.get("items", []) if isinstance(diag, dict) else []
        self._emit(
            AgentCoreInvocation(
                session_id=session_id,
                source="AGENTCORE",
                model_id=self._model_id,
                runtime_arn_tail=arn_tail,
                latency_ms=int((time.time() - started) * 1000),
                finding_count=finding_count,
                dispositions=tuple(
                    (i.get("disposition", "?") if isinstance(i, dict) else "?")
                    for i in items
                ),
            )
        )
        return diag


__all__ = ["AgentCoreSemanticModel", "AgentCoreInvocation"]
