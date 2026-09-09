"""Run the bounded M3a Strands provider experiment and emit one JSON receipt."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aircheck.domain.catalog import get_measurement  # noqa: E402
from spikes.m3a.core import (  # noqa: E402
    CandidateRequirementBatch,
    build_extraction_prompt,
    build_tool_prompt,
    evaluate_batch,
    load_fixture,
    validate_with_retry,
)


DEFAULT_MODELS = {
    "bedrock": "us.amazon.nova-lite-v1:0",
    "gemini": "gemini-2.5-flash",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run only the fixed, bounded AIRCheck M3a provider spike."
    )
    parser.add_argument("--provider", required=True, choices=("bedrock", "gemini"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model-id")
    parser.add_argument("--aws-profile", default="aircheck-bedrock")
    parser.add_argument("--aws-region", default="us-east-1")
    parser.add_argument("--google-location", default=os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
    parser.add_argument("--output", type=Path)
    return parser


def _google_project() -> str:
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if project:
        return project
    adc = Path(os.environ["APPDATA"]) / "gcloud" / "application_default_credentials.json"
    if adc.is_file():
        payload = json.loads(adc.read_text(encoding="utf-8"))
        project = payload.get("quota_project_id")
    if not project:
        raise RuntimeError(
            "Gemini Vertex mode needs GOOGLE_CLOUD_PROJECT or an ADC quota_project_id"
        )
    return str(project)


def _model_factory(args: argparse.Namespace) -> tuple[Callable[[], Any], dict[str, Any]]:
    model_id = args.model_id or DEFAULT_MODELS[args.provider]
    if args.provider == "bedrock":
        import boto3
        from botocore.config import Config
        from strands.models import BedrockModel

        def create_model() -> Any:
            session = boto3.Session(
                profile_name=args.aws_profile,
                region_name=args.aws_region,
            )
            return BedrockModel(
                boto_session=session,
                boto_client_config=Config(
                    connect_timeout=10,
                    read_timeout=120,
                    retries={"max_attempts": 1, "mode": "standard"},
                ),
                model_id=model_id,
                temperature=0,
                max_tokens=8192,
            )

        config = {
            "provider": "bedrock",
            "provider_path": "strands.models.BedrockModel",
            "model_id": model_id,
            "credential_source": f"AWS profile {args.aws_profile}",
            "region": args.aws_region,
            "temperature": 0,
            "max_tokens": 8192,
        }
        return create_model, config

    from strands.models.gemini import GeminiModel

    project = _google_project()

    def create_model() -> Any:
        return GeminiModel(
            client_args={
                "vertexai": True,
                "project": project,
                "location": args.google_location,
            },
            model_id=model_id,
            params={"temperature": 0, "max_output_tokens": 8192},
        )

    config = {
        "provider": "gemini",
        "provider_path": "strands.models.gemini.GeminiModel",
        "model_id": model_id,
        "credential_source": "Google application-default credentials (project redacted)",
        "vertex_ai": True,
        "location": args.google_location,
        "temperature": 0,
        "max_output_tokens": 8192,
    }
    return create_model, config


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
            if str(key) != "reasoningSignature"
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)


def _tool_metric_summary(value: Any) -> dict[str, Any]:
    raw = _json_safe(value)
    if not isinstance(raw, dict):
        return {}
    summarized: dict[str, Any] = {}
    for name, details in raw.items():
        if not isinstance(details, dict):
            continue
        item = {
            key: details[key]
            for key in ("call_count", "error_count", "success_count", "total_time")
            if key in details
        }
        tool = details.get("tool")
        if isinstance(tool, dict) and "input" in tool:
            item["input"] = tool["input"]
        summarized[name] = item
    return summarized


def _receipt_text(value: Any) -> str:
    text = str(value)
    text = re.sub(
        r"<(?P<tag>thinking|analysis)\b[^>]*>.*?</(?P=tag)>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = text.strip()
    if text == "lookup complete":
        return text
    return "<omitted: non-contractual provider response>"


def _metrics(result: Any, elapsed: float) -> dict[str, Any]:
    metrics = result.metrics
    usage = _json_safe(metrics.accumulated_usage)
    return {
        "elapsed_seconds": round(elapsed, 3),
        "usage": usage,
        "cycle_durations_seconds": _json_safe(metrics.cycle_durations),
        "tool_metrics": _tool_metric_summary(metrics.tool_metrics),
        "monetary_cost_available_in_provider_response": any(
            "cost" in str(key).casefold() for key in usage
        )
        if isinstance(usage, dict)
        else False,
    }


def _structured_call(create_model: Callable[[], Any], prompt: str) -> tuple[Any, CandidateRequirementBatch, dict[str, Any]]:
    from strands import Agent

    agent = Agent(
        model=create_model(),
        system_prompt=(
            "You extract candidate technical requirements. Follow the closed vocabulary and "
            "source-citation instructions exactly. Do not claim that candidates are verified."
        ),
        callback_handler=None,
    )
    started = time.perf_counter()
    result = agent(
        prompt,
        structured_output_model=CandidateRequirementBatch,
        limits={"turns": 4, "output_tokens": 12000, "total_tokens": 24000},
    )
    elapsed = time.perf_counter() - started
    output = result.structured_output
    if output is None:
        raise RuntimeError("Strands returned no structured output")
    batch = (
        output
        if isinstance(output, CandidateRequirementBatch)
        else CandidateRequirementBatch.model_validate(output)
    )
    return result, batch, _metrics(result, elapsed)


def _run_repeated(
    create_model: Callable[[], Any],
    prompt: str,
    runs: int,
) -> tuple[list[dict[str, Any]], bool]:
    observations: list[dict[str, Any]] = []
    for run_number in range(1, runs + 1):
        _, batch, metrics = _structured_call(create_model, prompt)
        assessment = evaluate_batch(batch)
        observations.append(
            {
                "run": run_number,
                "schema_valid": True,
                "usefulness": assessment.model_dump(mode="json"),
                "metrics": metrics,
                "output": batch.model_dump(mode="json"),
            }
        )
    return observations, all(item["usefulness"]["useful"] for item in observations)


def _run_tool_round_trip(
    create_model: Callable[[], Any],
) -> dict[str, Any]:
    from strands import Agent, tool

    calls: list[str] = []

    @tool
    def lookup_measurement_definition(measurement_key: str) -> dict[str, str | None]:
        """Return the frozen AIRCheck definition for one measurement key.

        Args:
            measurement_key: Exact closed-catalog measurement key.
        """

        calls.append(measurement_key)
        definition = get_measurement(measurement_key)
        return {
            "measurement_key": definition.measurement_key,
            "label": definition.label,
            "canonical_unit": definition.canonical_unit,
            "produced_by": definition.produced_by,
        }

    agent = Agent(
        model=create_model(),
        tools=[lookup_measurement_definition],
        system_prompt="Perform exactly the requested read-only catalog lookup.",
        callback_handler=None,
    )
    started = time.perf_counter()
    result = agent(
        build_tool_prompt(),
        limits={"turns": 3, "output_tokens": 4096, "total_tokens": 12000},
    )
    elapsed = time.perf_counter() - started
    return {
        "success": calls == ["audio.integrated_loudness"],
        "calls": calls,
        "response": _receipt_text(result),
        "metrics": _metrics(result, elapsed),
    }


def _run_malformed_retry(
    create_model: Callable[[], Any],
    extraction_prompt: str,
) -> dict[str, Any]:
    from strands import Agent

    provider_attempts: list[dict[str, Any]] = []

    def operation(attempt: int) -> Any:
        if attempt == 1:
            agent = Agent(
                model=create_model(),
                system_prompt="Return the requested fault-injection sentinel only.",
                callback_handler=None,
            )
            started = time.perf_counter()
            result = agent(
                "Reply exactly MALFORMED_CANDIDATE_OUTPUT. The fixed extraction fixture "
                f"under test follows, but do not process it:\n\n{extraction_prompt}",
                limits={"turns": 1, "output_tokens": 128, "total_tokens": 12000},
            )
            elapsed = time.perf_counter() - started
            provider_attempts.append(
                {
                    "attempt": 1,
                    "mode": "controlled malformed-output injection",
                    "provider_response": _receipt_text(result),
                    "metrics": _metrics(result, elapsed),
                }
            )
            return "CONTROLLED_MALFORMED_PREFIX:" + str(result)

        _, batch, metrics = _structured_call(create_model, extraction_prompt)
        provider_attempts.append(
            {
                "attempt": 2,
                "mode": "typed retry",
                "metrics": metrics,
            }
        )
        return batch

    batch, retry = validate_with_retry(operation, max_attempts=2)
    assessment = evaluate_batch(batch)
    return {
        "success": retry.attempts == 2 and assessment.useful,
        "fault_injection": (
            "The first real provider response was prefixed at the integration boundary to "
            "guarantee malformed CandidateRequirementBatch input; this is controlled retry "
            "evidence, not a spontaneous provider defect."
        ),
        "retry": retry.model_dump(mode="json"),
        "recovered_usefulness": assessment.model_dump(mode="json"),
        "provider_attempts": provider_attempts,
    }


def main() -> int:
    args = _parser().parse_args()
    if args.runs != 3:
        raise SystemExit("M3a requires exactly three repeated structured-output runs")

    create_model, configuration = _model_factory(args)
    document, segments = load_fixture()
    prompt = build_extraction_prompt()
    repeated, repeated_success = _run_repeated(create_model, prompt, args.runs)
    tool_round_trip = _run_tool_round_trip(create_model)
    malformed_retry = _run_malformed_retry(create_model, prompt)
    versions = {
        package: importlib.metadata.version(package)
        for package in ("strands-agents", "pydantic")
    }
    if args.provider == "bedrock":
        versions.update(
            boto3=importlib.metadata.version("boto3"),
            botocore=importlib.metadata.version("botocore"),
            awscrt=importlib.metadata.version("awscrt"),
        )
    else:
        versions.update(
            google_genai=importlib.metadata.version("google-genai"),
            google_auth=importlib.metadata.version("google-auth"),
        )

    receipt = {
        "gate": "M3a",
        "experiment": "bounded Strands provider spike",
        "configuration": configuration,
        "versions": versions,
        "fixture": {
            "path": "spikes/m3a/fixture.md",
            "sha256": hashlib.sha256(document.text.encode("utf-8")).hexdigest(),
            "segment_count": len(segments),
            "segment_ids": [segment.segment_id for segment in segments],
        },
        "repeated_runs": repeated,
        "repeat_summary": {
            "requested": args.runs,
            "schema_valid": len(repeated),
            "useful": sum(
                1 for item in repeated if item["usefulness"]["useful"]
            ),
        },
        "tool_round_trip": tool_round_trip,
        "malformed_output_retry": malformed_retry,
        "available_cost_usage_evidence": (
            "Per-invocation latency and provider token usage are recorded. No monetary-cost "
            "field was available in the Strands provider responses; no cost was fabricated."
        ),
        "provider_acceptance": (
            repeated_success
            and tool_round_trip["success"]
            and malformed_retry["success"]
        ),
    }
    rendered = json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        evidence_root = (REPOSITORY_ROOT / "docs" / "evidence" / "M3a").resolve()
        output = args.output.resolve()
        if output.parent != evidence_root:
            raise SystemExit("--output must be a direct child of docs/evidence/M3a")
        output.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"receipt_path={output.relative_to(REPOSITORY_ROOT).as_posix()}")
        print(f"receipt_sha256={hashlib.sha256(rendered.encode('utf-8')).hexdigest()}")
        print(f"provider_acceptance={receipt['provider_acceptance']}")
    return 0 if receipt["provider_acceptance"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
