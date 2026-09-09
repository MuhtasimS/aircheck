# M3a provider spike

This directory is a bounded experiment, not AIRCheck product runtime. It uses one immutable six-clause fixture, emits `CandidateRequirementBatch` through the frozen R2 candidate schema, and judges usefulness with deterministic `admit()` calls.

## Reproduce

Create an isolated virtual environment outside the repository, install `requirements.txt`, then run the provider command. The commands below intentionally require exactly three repeated extraction runs.

```powershell
python -m pip install -r spikes/m3a/requirements.txt
$env:AWS_PROFILE = "aircheck-bedrock"
$env:AWS_REGION = "us-east-1"
python spikes/m3a/run_spike.py --provider bedrock --runs 3 --model-id us.amazon.nova-lite-v1:0 --output docs/evidence/M3a/M3a.2_bedrock_spike.json
python spikes/m3a/run_spike.py --provider gemini --runs 3 --model-id gemini-2.5-flash --output docs/evidence/M3a/M3a.3_gemini_spike.json
```

Bedrock uses the Boto3 profile chain. Gemini uses Google application-default credentials in Vertex AI mode and reads `GOOGLE_CLOUD_LOCATION` (the measured run used `us-central1`). Project/account identifiers are intentionally redacted from receipts.

Each provider run performs:

1. three independent structured-output calls;
2. deterministic usefulness evaluation for all six candidates;
3. exactly one read-only measurement-catalog tool round trip; and
4. a controlled malformed-output injection followed by at most one typed retry.

The controlled malformed case proves the integration retry boundary. It is not represented as a spontaneous provider defect. Latency and token usage come from Strands metrics; neither provider response exposed a monetary-cost field, so no cost estimate is invented.

Provider API references: [Strands Bedrock](https://strandsagents.com/docs/user-guide/concepts/model-providers/amazon-bedrock/), [Strands Gemini](https://strandsagents.com/docs/user-guide/concepts/model-providers/google/), [Strands structured output](https://strandsagents.com/docs/user-guide/concepts/agents/structured-output/), and [Strands metrics](https://strandsagents.com/docs/user-guide/observability-evaluation/metrics/).
