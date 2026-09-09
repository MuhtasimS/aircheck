# AIRCheck S3 diagnosis agent (AgentCore Runtime)

A minimal Strands agent hosted on Amazon Bedrock AgentCore Runtime that performs
AIRCheck's bounded **S3 diagnosis** step: given typed findings and their
already-authorized remediation options, it recommends a disposition per finding
(APPLIED tier-1 / ESCALATE tier-2 / NO_ACTION). The AIRCheck runtime re-validates
every recommendation (`diagnose_findings`) and owns all authority and terminal
truth — the agent never mints authority, sees a path, or decides readiness.

- Entrypoint: `main.py` (`BedrockAgentCoreApp`, `@app.entrypoint`, HTTP `/invocations` + `/ping`).
- Model: a Bedrock inference profile (default `us.amazon.nova-lite-v1:0`), set via `AIRCHECK_AGENTCORE_MODEL_ID`.
- Deployed CodeZip via `python deploy/deploy.py agentcore` (direct `create_agent_runtime`; no CDK, no local Docker).
- Invoked by the AIRCheck API via `bedrock-agentcore.invoke_agent_runtime`; untrusted output validated on the AIRCheck side with a visible deterministic fallback.
