# AIRCheck security and authority policy

## R2 posture

R2 is a local deterministic foundation. It has no authentication, public API, upload pipeline, model/provider invocation, real media execution, cloud deployment, external delivery integration, or production secrets. All nine media/action tools are typed, truthfully unimplemented stubs.

## Non-negotiable asset rules

- Original inputs are immutable and may be read by Tier-0 inspection only.
- AIRCheck never overwrites or deletes an original master.
- Tier-1 work targets a `WORKING` copy or creates a successor record.
- Tier-2 work creates a `DERIVATIVE` and requires one exact human-authorized attempt.
- Every runtime path is resolved from an ID inside the run workspace; parent traversal and absolute paths are rejected.
- OS read-only flags are best-effort hardening. Terminal hash verification is the proof that originals remain unchanged.
- Unknown tools, wrong states, unbound options, mismatched arguments, and invented operations are denied by default.
- AIRCheck never claims successful delivery to an external broadcaster, invents missing factual metadata, or silently resolves contradictory requirements.

## Authority tiers

| Tier | Policy | R2 contract |
| --- | --- | --- |
| 0 — inspection | Autonomous in inspection states; read-only | Five inspection ToolSpecs |
| 1 — reversible | Exact open-finding option and canonical arguments; working/derivative target only | Three mechanical ToolSpecs |
| 2 — content affecting | Explicit human decision; opaque persisted authorization; one exact attempt | Normalized-audio derivative ToolSpec |
| 3 — forbidden | Never callable | Enforced by registry absence, state/option checks, and path/original guards |

Tier-2 authorization IDs are randomly minted at runtime, never derived from public fields, bound to exact `(run_id, option_id, canonical_args_hash)`, independently verified, and atomically consumed before `ACTION_STARTED` can lead to future I/O. The deterministic argument hash is binding identity only; it is not a capability or secret. Failed or repeated consumption is refused.

## Side-effect ordering

Future mutating tool bodies must receive a validated `ActionContext`. The executor requires `REMEDIATING`, checks the option and target, consumes Tier-2 authority when applicable, persists `ACTION_STARTED`, and only then may a later gate perform I/O. Direct mutating-stub calls currently fail and touch nothing.

The local store uses atomic file replacement and exclusive lock files for snapshots, ledgers, decisions, and authorizations. It identifies `ACTION_STARTED` entries with no terminal event so a later media/runtime gate can reconcile temporary or promoted outputs before resuming.

## Dependency advisories

The current pinned frontend install reports 11 dependency advisories: 1 low, 2 moderate, and 8 high. R2 does not force dependency upgrades without compatibility evidence; this remains an acknowledged compatibility follow-up outside this public mirror. Vinext also emits upstream deprecation and optimizer warnings while the declared tests, build, and route smoke checks pass.

## Reporting a vulnerability

Open a private security report through the repository host when available. Until a private channel is configured, do not include exploit details, credentials, media, or customer data in a public issue.
