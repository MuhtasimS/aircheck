# AIRCheck product contract

**Status:** FROZEN for v1. Changes require a dated `DECISIONS.md` entry authorized by mission control.

## Product definition

AIRCheck is an autonomous media-delivery agent that interprets destination specifications, plans and executes technical QC, safely remedies routine defects, escalates changes requiring human authority, re-verifies the package, and produces evidence that a delivery is ready.

**Primary user:** post-production professional, finishing producer, or delivery coordinator.

**Hero promise:** The master is finished. The delivery isn't.

**Hero outcome:** Given a preserved media package and a human-readable destination specification bundle, AIRCheck gets the package to a provable terminal state—`DELIVERY_READY` or `BLOCKED`—with receipts. `FAILED` is reserved for a truthful system/runtime failure and is never rendered as readiness.

## Frozen doctrine

> **Model agency, deterministic authority.**

The core chain is:

```text
Prose → predicates → facts → authority → evidence
```

One principal Strands agent may eventually contribute semantic judgment at exactly three bounded points:

1. interpret source prose into untrusted candidate requirements;
2. propose ordering/grouping over a mandatory deterministic check set;
3. diagnose open findings and select among typed remediation options or escalate.

The deterministic runtime owns identity, admission, required coverage, state transitions, measurements, predicate results, authority, filesystem side effects, evidence, and terminal verdicts. The model never declares that media passed, lowers severity or authority, invents applicability, writes filesystem paths, or changes state directly.

## User-visible workflow

1. The user supplies a media package and a destination specification bundle.
2. AIRCheck preserves and hashes originals, creates a working set, and records source provenance.
3. Candidate requirements are admitted, rejected, or carried forward with named normalization outcomes.
4. Every executable and applicable requirement becomes a required check; a semantic plan may reorder but cannot shrink coverage.
5. Deterministic tools measure package facts and deterministic predicates evaluate them.
6. Safe, option-bound mechanical changes may run against working copies.
7. Content-affecting derivative work pauses for a bounded human decision and a runtime-minted, single-use authorization.
8. Every changed package is re-inspected. Terminal logic re-hashes originals and alone computes `DELIVERY_READY` or `BLOCKED`.
9. The user receives the causal ledger, QC report, manifest/checksums, lineage, measurements, and generated derivatives that actually exist.

## Trust commitments

- Originals are inspectable but never overwritten or deleted.
- Blocking ambiguous, contradictory, unsupported, externally dependent, or unproven-applicability requirements cannot silently disappear into green.
- Tool failure yields `NOT_EVALUATED` or `FAILED`, never PASS.
- Approval is bound to one exact run, option, and canonical argument set for one attempt.
- AIRCheck never invents factual metadata, silently resolves contradictory human requirements, or claims external broadcaster delivery.
- Evidence never claims a side effect before the verified artifact exists.
- Product-safe causal events are recorded; hidden chain-of-thought is not.

## Authority boundaries

| Tier | AIRCheck v1 behavior |
| --- | --- |
| Tier 0 — inspection | Autonomous, read-only, any run asset |
| Tier 1 — reversible | Autonomous only for a bound option writing a working copy or new artifact |
| Tier 2 — content-affecting derivative | Requires explicit human decision plus an opaque, persisted, runtime-minted single-use authorization |
| Tier 3 — forbidden | Never overwrite/delete originals, invent facts, silently resolve contradictions, or claim external delivery |

## Frozen product surfaces

AIRCheck has five surfaces and no chat prompt:

1. **Deliveries** — cross-run operational ledger and truthful loading/empty/error/populated states.
2. **New Delivery** — one media package plus one destination specification bundle.
3. **Delivery Control Room** — requirements, chronological execution, and original/working/derivative package lineage.
4. **Human Decision** — measured/required values, proposed content change, consequences, and explicit approve/block outcomes.
5. **Evidence** — terminal counts, final requirement ledger, causal events, lineage, and downloadable artifacts that exist.

The existing dark editorial visual system is frozen through R2. Later gates may wire real projections and commands, then perform the explicitly deferred presentation/accessibility polish.

## Synthetic universe

- Fictional program: **The Last Lightkeeper**.
- Fictional destination: **Northstar Broadcast Network**.
- Valid profiles: **Northstar Broadcast Master** and **Northstar Digital Preview**.
- Product proof: the same source package plus a different destination specification produces a different required QC plan.

Adversarial specification behavior belongs in dedicated evaluation fixtures, not in either valid destination profile.

## v1 non-goals

- multiple collaborating agents or A2A;
- model-authored measurements, terminal verdicts, authority grants, or applicability exceptions;
- LLM-as-judge correctness;
- persisted agent conversation/session resume after human approval;
- automatic semantic subtitle generation or editorial caption rewriting;
- video transcoding as a Tier-2 remediation;
- external delivery submission to a broadcaster;
- fetching missing external reference documents;
- waiver/clarification editing inside a blocked run;
- generalized policy languages, Merkle trees, signatures, or notarization;
- decorative animation before the truthful product loop exists.

## Release boundary

Workers execute only the currently released scope. R2 is foundation migration only. Media truth, model/provider work, synthetic generation, headless execution, API/UI truth wiring, evaluation execution, deployment, release polish, and irreversible submission each remain in their named future gates.
