# AIRCheck architectural decisions

This log is append-only. Every entry records a frozen decision, its reason, and the gate/evidence that authorized it. Proposed changes remain marked PROPOSED until mission control accepts them.

## 2026-09-04 — D-001: Model agency, deterministic authority

- **Status:** ACCEPTED
- **Decision:** The deterministic runtime calls one semantic agent at S1 interpretation, S2 plan proposal, and S3 diagnosis. The agent never owns state, factual measurements, admission, authorization, side effects, predicate evaluation, or terminal verdicts.
- **Reason:** This preserves useful model judgment while making correctness and authority auditable.
- **Source:** Approved consolidated plan §§B–H; R2 authorization.

## 2026-09-04 — D-002: Loss-aware, fail-closed requirement admission

- **Status:** ACCEPTED
- **Decision:** Normalization outcomes are `EXECUTABLE`, `AMBIGUOUS`, `CONTRADICTORY`, `UNSUPPORTED`, and `EXTERNAL_DEPENDENCY`. Blocking non-executable or unresolved-applicability requirements prevent readiness. Severity comes from deterministic modal parsing of the source; model confidence/proposals cannot lower it. Applicability uses a closed polarity-verified grammar.
- **Reason:** Human requirements must never silently vanish or be relaxed by a model.
- **Source:** Approved consolidated plan §§D, E, G; R2 authorization.

## 2026-09-04 — D-003: Eight constraint operators and closed measurement catalog

- **Status:** ACCEPTED
- **Decision:** The v1 operators are exactly `EQUALS`, `MIN`, `MAX`, `RANGE`, `ONE_OF`, `MATCHES_PATTERN`, `PRESENT`, and `ABSENT`. Executability requires a known measurement key, canonical unit/value, supported operator, source provenance, and complete constraint shape.
- **Reason:** A closed executable language gives deterministic ground truth and keeps prose out of the verifier.
- **Source:** Approved consolidated plan §G; R2 authorization.

## 2026-09-04 — D-004: Frozen lifecycle and terminal choke point

- **Status:** ACCEPTED
- **Decision:** The lifecycle uses `CREATED`, `INGESTING`, `INGESTED`, `INTERPRETING`, `REQUIREMENTS_ADMITTED`, `QC_PLANNED`, `INSPECTING`, `FINDINGS_READY`, `REMEDIATING`, `AWAITING_HUMAN_DECISION`, `DELIVERY_READY`, `BLOCKED`, and `FAILED`. Re-verification is `INSPECTING` with `cycle >= 1`; authorization is a capability, not a state. Only `terminal.compute()` may construct a `TerminalVerdict` and authorize a terminal edge.
- **Reason:** State names describe durable run truth; capability and cycle concepts do not belong as states.
- **Source:** Approved consolidated plan §E; R2 authorization.

## 2026-09-04 — D-005: Opaque single-use authorization capability

- **Status:** ACCEPTED
- **Decision:** Tier-2 authorization is a runtime-minted opaque identifier, persisted by an authority store, bound to exact `(run_id, option_id, canonical_args_hash)`, independently verified and atomically consumed before I/O, and never derived predictably from public fields. The canonical-argument hash is binding identity only, not the capability.
- **Reason:** Publicly reproducible fields must not confer permission; one human decision authorizes one exact attempt.
- **Source:** Approved consolidated plan §§D, F plus mission-control R2 amendment 2.

## 2026-09-04 — D-006: Nine agent-facing tools

- **Status:** ACCEPTED
- **Decision:** Agent-facing tools are exactly `scan_package`, `probe_media`, `measure_loudness`, `inspect_captions`, `verify_manifest`, `rename_delivery_copy`, `write_checksum_manifest`, `convert_caption_format`, and `create_normalized_audio_derivative`. Hashing, filename predicates, decision creation, and finalization are runtime responsibilities.
- **Reason:** The agent should select meaningful work, not decorative runtime functions.
- **Source:** Approved consolidated plan §H; R2 authorization.

## 2026-09-04 — D-007: Snapshot plus append-only log behind RunStore

- **Status:** ACCEPTED
- **Decision:** Development through M4 uses `LocalDurableStore`; production later uses `S3RunStore` through the same interface. Events append monotonically and snapshots must agree with the latest transition. Container-local disk is disposable workspace/cache in production.
- **Reason:** Local restart semantics can be proven before deployment without making local disk a production authority.
- **Source:** Approved consolidated plan §§B, D, K, L; R2 authorization.

## 2026-09-04 — D-008: Preserve the five-surface visual system

- **Status:** ACCEPTED
- **Decision:** R2 keeps the existing Deliveries, New Delivery, Control Room, Human Decision, and Evidence visual shell and its disclosed mock/inert behavior unchanged except for regression verification. Runtime/API truth wiring is M5; presentation polish is M8.
- **Reason:** The M0 information architecture is already compatible, and truth must precede polish.
- **Source:** R1 classification accepted by mission control; R2 authorization.

## 2026-09-04 — D-009: Approved consolidated plan retained as input evidence

- **Status:** ACCEPTED
- **Decision:** The externally approved plan is preserved byte-identically at `docs/evidence/R2/input/CONSOLIDATED_PLAN.md`, SHA-256 `c813fccfeca3a62ec9eacba2c9bf3a679c9fed60c68174378341737fca11defc`. It is immutable input evidence. Post-R2 authority lives in the §N canonical repository documents.
- **Reason:** Future workers can prove what R2 translated without treating a historical input as live state.
- **Source:** Mission-control R2 amendment 4.

## 2026-09-04 — D-010: Canonical runner exposes real operations only

- **Status:** ACCEPTED
- **Decision:** R2's cross-platform runner exposes only real foundation operations such as install, test, lint, frontend build, and integrated verification. It must not add a stub/fake `demo` command; that command appears only when a later gate owns a reproducible demo.
- **Reason:** A command name must correspond to truthful executable behavior.
- **Source:** Mission-control R2 amendment 1.

## 2026-09-04 — D-011: Historical M0 remains untagged

- **Status:** ACCEPTED
- **Decision:** Do not backfill `gate/M0`. Record `86fbf8d58191c060ea76a1187a2ab6d3e3be8029` as the verified historical baseline. Formal gate tags begin with `gate/R1`.
- **Reason:** Preserve historical fact rather than retroactively rewriting gate convention.
- **Source:** Mission-control R2 amendment 3.

## 2026-09-04 — D-012: No persisted agent-loop resume

- **Status:** ACCEPTED
- **Decision:** Raw Strands interrupts eventually signal the pause, but app-level authority owns it. After approval, the deterministic runtime executes the already-bound option and re-enters inspection; it does not resume a persisted model conversation. Blanket/trust approval is forbidden.
- **Reason:** The approved action is fully specified and restart safety belongs to durable run state, not model-session continuity.
- **Source:** Approved consolidated plan §F.

## 2026-09-04 — D-013: Unresolved specifications produce a new run

- **Status:** ACCEPTED
- **Decision:** `BLOCKED` is terminal. Waiver, clarification, or supplied external reference creates a new run with an amended specification bundle; v1 has no in-run specification-resolution loop.
- **Reason:** AIRCheck refuses to certify language it cannot execute and preserves the exact blocked receipt.
- **Source:** Approved consolidated plan §E.6.

## 2026-09-06 — D-014: M3a provider result selects Gemini 2.5 Flash

- **Status:** ACCEPTED
- **Decision:** For a future authorized M3 implementation, the proven semantic-provider configuration is Strands `GeminiModel` in Vertex AI mode with `gemini-2.5-flash`. The tested Bedrock configuration, Strands `BedrockModel` with `us.amazon.nova-lite-v1:0` in `us-east-1`, is not an accepted fallback under the M3a usefulness bar. Any future Bedrock fallback requires separately authorized model/prompt evidence; deterministic M3 fallback remains mandatory and unimplemented.
- **Evidence:** Both providers ran the same fixture SHA-256 `caa50834ddd54cd8a4e43a89284cf495bc8e3d30531bc415a4bbe3c8c6bd6812` through the same harness. Gemini returned 3/3 schema-valid and deterministically useful batches, one exact successful tool call, and successful typed recovery after a controlled malformed-output injection. Nova Lite returned 3/3 schema-valid but 0/3 deterministically useful batches, made the exact tool call, and failed usefulness after retry. See `docs/evidence/M3a/M3a.2_bedrock_spike.json`, `M3a.3_gemini_spike.json`, and `M3a.4_comparison.txt`.
- **Boundary:** This is a comparison of the two exact tested configurations, not a conclusion about either provider family. No production provider integration is authorized or implemented by this decision.
- **Source:** M3a authorization and canonical `ROADMAP.md` acceptance criteria.

## 2026-09-08 — D-015: M6 evaluates v1 conditional applicability as it exists; runtime NOT_APPLICABLE is a documented gap

- **Status:** ACCEPTED
- **Decision:** The M6 evaluation corpus evaluates the product that actually exists. AIRCheck v1 does not implement runtime `content_type` applicability resolution and never produces `NOT_APPLICABLE`; deterministic admission validates a condition's grammar/polarity and marks a requirement `APPLICABLE` (its check then always runs) or `UNRESOLVED`. The corpus therefore covers the real admission/applicability grammar and polarity/error behavior (supported `NEQ` polarity admitted `APPLICABLE`; reversed polarity downgraded to `UNRESOLVED` via `APPLICABILITY_POLARITY_UNPROVEN`; unsupported applicability field `UNRESOLVED`) and mixed-modal fail-closed severity as a separate, unconditional case. The canonical corpus's applicable/non-applicable `content_type` polarity branch (`docs/evidence/R2/input/CONSOLIDATED_PLAN.md` §G.3 scenario 6 / §I.4, which requires runtime `NOT_APPLICABLE`) is recorded as **not implemented in v1 / evaluated as a documented capability gap**, not relabelled as equivalent coverage. M6 does not modify M3/M4 runtime applicability semantics.
- **Reason:** M6 is evaluation and reliability evidence, not new runtime semantics. Implementing runtime `NOT_APPLICABLE` would be an M3/M4 product change outside M6 scope; fabricating it in the corpus is forbidden. The honest record is what the product does plus a named gap.
- **Source:** Mission-control M6 REPAIR decision 2026-09-08 (supersedes the earlier instruction to force literal applicable/non-applicable runtime branches). Independent Sol audit finding on canonical corpus coverage.

## 2026-09-08 — D-016: Canonical procedure for superseding a false-green gate tag

- **Status:** ACCEPTED
- **Decision:** When an already-closed gate tag is later shown false-green by independent audit, it is superseded (not deleted, not history-rewritten) by this procedure: (1) before any change, create an annotated immutable `gate/<ID>-superseded` tag at the original closing commit whose message records the invalidation and points to the preserved RED audit receipt; (2) complete the bounded repair on new commits; (3) only after the repaired gate genuinely closes GREEN, repoint the annotated canonical `gate/<ID>` to the repaired closing commit; (4) record the supersession in `BUILD_STATE.md` and the narrow permitted gate-state bookkeeping in `ROADMAP.md`; (5) preserve the old commit, old receipts, the audit, and the repair receipts permanently and rewrite no commit history. Future workers treat `gate/<ID>` as the current valid gate and `gate/<ID>-superseded` as historical evidence. If `gate/<ID>` was pushed to a shared/public remote such that moving it would require destructive remote tag rewriting, stop before changing the remote and report to mission control; local tag supersession is authorized.
- **Reason:** The worker contract defined one gate-closing commit/tag and forbade rewriting history but provided no mechanism to supersede a false-green tag. This establishes one, keeping all evidence immutable while making the canonical tag point at genuinely verified work.
- **Evidence:** First application — `gate/M6-superseded` -> `41ed40cb2b8ab001b591d513f1ce821f8a74fe58` (original M6 close); RED audit `docs/evidence/M6/audit/M6_INDEPENDENT_AUDIT_RED.md`.
- **Source:** Mission-control M6 REPAIR decision 2026-09-08.

## 2026-09-08 — D-017: Ordinal immutable tags for repeated false-green supersession

- **Status:** ACCEPTED
- **Decision:** When a gate is superseded more than once (a repair is itself found false-green by a later audit), each superseded close is preserved under an ordinal immutable annotated tag: the first at `gate/<ID>-superseded`, the second at `gate/<ID>-superseded-2`, and so on (`-3`, `-4`, ...), each anchored at that close's commit with a message recording the invalidating audit. `gate/<ID>` always points to the current genuinely-GREEN close; the ordinal tags are historical evidence, newest ordinal = most recent superseded repair. No commit history is rewritten and no prior superseded tag is moved. This extends D-016 for the repeated case.
- **Evidence:** Second application — `gate/M6-superseded-2` -> `352c3b670eccfb40171865461dfd07cfabe0c267` (first repaired M6 close); second RED audit `docs/evidence/M6/audit/M6_INDEPENDENT_AUDIT_2_RED.md`. `gate/M6-superseded` remains unchanged at `41ed40c`.
- **Source:** Mission-control M6 REPAIR-2 decision 2026-09-08.

## 2026-09-08 — D-018: M7 deploys under root with zero new long-lived credentials

- **Status:** ACCEPTED
- **Decision:** The authorized AWS profile resolves to the account **root** user, and AWS forbids root from assuming IAM roles ("Roles may not be assumed by root accounts"), so a role + source-profile deployer cannot be bootstrapped without a new IAM user + access key. Mission control chose to **proceed under root for M7 control-plane orchestration this session and create no new long-lived credentials**. All AWS *service* execution roles (Lambda, CodeBuild, AgentCore runtime) are least-privilege. A broad admin role `aircheck-m7-deployer` was created as the M8 non-root-admin scaffold; it is currently inert (no IAM user exists and root cannot assume it). This is a deliberate, documented deviation from "do not use root for normal deployment work," mitigated by creating zero new standing credentials and scoping every service role; a proper non-root admin/SSO identity is deferred to M8 hardening.
- **Reason:** Honoring "avoid new long-lived access keys" and the mission-control instruction, while keeping the blast radius of standing credentials at zero and service roles least-privilege.
- **Source:** M7 mission-control authorization 2026-09-08 §4; recorded decision "Proceed under root, no new keys."

## 2026-09-08 — D-019: M7 deployment topology — Lambda container + API Gateway (Fargate-compatible)

- **Status:** ACCEPTED
- **Decision:** The M7 public deployment is a **portable OCI container on AWS Lambda behind an API Gateway HTTP API** (public HTTPS with no custom domain), for both the API (FastAPI + ffmpeg, via the AWS Lambda Web Adapter) and the frontend (`vinext start` Node server). This deviates from the canonical ECS Fargate direction in `ARCHITECTURE.md`/`ROADMAP.md`. Rationale, in order of force: (a) the authorized identity is root, and **AWS App Runner is blocked under root** (`SubscriptionRequiredException`); (b) **public Lambda Function URLs are blocked by an account-level restriction**, so API Gateway is the public ingress; (c) Fargate cannot provide public HTTPS on an AWS hostname without a custom domain (forbidden) or added CloudFront/ALB, which the mission's simplicity + no-custom-domain constraints disfavor; (d) Lambda gives native managed HTTPS, cloud (CodeBuild) builds with no local Docker, scale-to-zero cost, and naturally exercises the canonical "disposable disk + durable S3" model. The container is a normal web server and remains **Fargate-compatible**, preserving the canonical intent in substance. Durable production state uses the canonical **`S3RunStore`** (D-007), implemented as a non-invasive mirror that preserves every frozen `LocalDurableStore` WAL/authority/recovery semantic.
- **Reason:** Deliver the mission's hard requirements (public HTTPS, no custom domain, no local Docker, durable S3 state, simplest reproducible architecture) under the real account/identity constraints, without weakening any M4/M5/M6 invariant.
- **Evidence:** `docs/evidence/M7/M7.1_aws_preflight.txt` (App Runner + Function URL blocks), `M7.2_deployment.txt`, `M7.5_public_hero.json`, `M7.2b_durability.txt`.
- **Source:** M7 mission-control authorization 2026-09-08 §§6, 8, 12 (simplest reproducible architecture; canonical S3RunStore; record final topology).

## 2026-09-08 — D-020: M7 real AgentCore integration at the S3 diagnosis seam (Nova Lite; visible fallback)

- **Status:** ACCEPTED
- **Decision:** M7 implements a **real Amazon Bedrock AgentCore Runtime** component: a Strands agent (`deploy/agentcore/`) hosted on AgentCore Runtime that performs AIRCheck's bounded **S3 diagnose/select/escalate** step. The agent RECOMMENDS a disposition among the already-authorized, typed remediation options; the deterministic runtime (`diagnose_findings`) re-validates every recommendation and owns all authority and terminal truth. Only the `Diagnosis` output crosses the AgentCore boundary; S1/S2 stay deterministic. The deployed model is **Amazon Bedrock Nova Lite** (`us.amazon.nova-lite-v1:0`): Anthropic Claude was `NOT_AVAILABLE` without a one-time account access form, and the narrow bounded task plus deterministic validation + visible fallback make Nova sufficient; the model id is env-configurable. The agent is deployed as an **ARM64 container** (CodeZip's runtime did not install dependencies). On any AgentCore fault or non-bindable recommendation the semantic model falls back **visibly** (logged `source=FALLBACK`, `/health` `agentcore=fallback`) to the deterministic double, so a misbehaving model can never fail the run or cross the authority boundary. This is a mission-authorized amplification of the ROADMAP's "AgentCore optional and kill-switched" to a required real deliverable with a visible fallback; it does not supersede D-014 (that compared specific configurations for the different S1 interpretation task).
- **Reason:** AgentCore is a real M7 deliverable that must participate in the hero exactly where model agency legitimately belongs, without moving factual/authority/terminal truth into the model.
- **Evidence:** `docs/evidence/M7/M7.3_agentcore.txt`, `M7.4_observability.txt`; `aircheck/agent/agentcore_model.py`, `deploy/agentcore/`, `tests/agent/test_agentcore_model.py`.
- **Source:** M7 mission-control authorization 2026-09-08 §7 (AgentCore is a real M7 deliverable).

## 2026-09-08 — D-021: M7 silences one third-party test warning (anyio/Starlette environment drift)

- **Status:** ACCEPTED
- **Decision:** `pyproject.toml` adds a single targeted `filterwarnings` ignore for `anyio.abc.BlockingPortal is deprecated`. anyio >= 4.15 deprecated an alias that Starlette's `TestClient` still imports; under the suite's `filterwarnings = ["error"]` this turned into an error for the 17 FastAPI TestClient tests on the current (drifted) environment — reproducible on the clean `gate/M6` tree, i.e. not caused by M7. The ignore is scoped to that exact upstream message only; every AIRCheck test still runs and asserts, and AIRCheck code stays warning-clean.
- **Reason:** Keep `python scripts/project.py verify` GREEN reproducibly against a third-party deprecation, without weakening any AIRCheck assertion or masking our own warnings.
- **Source:** M7 mission-control authorization 2026-09-08 §22 (full verify GREEN); §10 (technical fixes required for the build).

## 2026-09-08 — D-022: M8 Phase A release-hardening — engineering decisions

- **Status:** ACCEPTED
- **Decision:** M8 Phase A hardens the live M7 deployment for judging without changing any M4/M5/M6 truth, redesigning the UI, or writing public prose. The concrete engineering choices:
  1. **Cross-container mutation safety** — a per-run advisory lease backed by S3 conditional writes (`If-None-Match: *` to create, `If-Match` to take over an expired lease), in `aircheck/persistence/s3_lock.py`. `submit_decision` holds the lease, re-reads authoritative state strictly under it, and mints exactly one decision; the loser receives a product-safe HTTP 409. Chosen over migrating persistence to DynamoDB because S3 conditional writes (live-verified on the versioned state bucket) satisfy the single-use-decision invariant with no new datastore and no change to the frozen `LocalDurableStore` semantics.
  2. **Cold-start / hydration split** — the per-request path hydrates only the small authoritative store index (~1 MB); each run's ~5 MB media/evidence workspace is hydrated lazily on access. Sync failures are logged and fail closed (503) rather than serving stale state; a cold container never pulls the whole ~106 MB tree. Chosen over deleting historical runs, which does not fix the structural cliff.
  3. **AgentCore malformed-output hardening** — the adapter validates untrusted output against the canonical typed `Diagnosis` model inside its protected boundary; any malformed/unbindable output or error envelope yields a truthful deterministic fallback, never an uncaught adapter exception or a stranded run. The `Diagnosis` schema is not loosened.
  4. **Durable AgentCore provenance** — a per-run, non-authority-bearing sidecar (`agentcore_provenance.json`) records semantic source (AGENTCORE|FALLBACK), an opaque session/correlation id, model id, dispositions, and any fallback reason, projected into the evidence response. It carries no chain-of-thought, prompt, or credential and never affects measurements, predicates, authority, or the terminal verdict — so a judge can distinguish AGENTCORE from FALLBACK from the evidence alone, not only CloudWatch.
  5. **Release identity** — all three images (API, web, AgentCore) are built from one engineering source commit and deployed pinned to that immutable release-SHA tag (`latest` kept only as a convenience alias); a machine-readable release manifest records Git SHA, image tags/digests, timestamp, environment, and model id.
  6. **Least-privilege service IAM** — the AgentCore execution role's `bedrock:InvokeModel` is narrowed from all foundation models to only the Nova Lite inference profile (`us.amazon.nova-lite-v1:0`) plus its backing foundation models (us-east-1/us-east-2/us-west-2). The never-used `aircheck-m7-deployer` AdministratorAccess scaffold (root cannot assume it; `RoleLastUsed` empty) is retired; a proper non-root/SSO admin identity, if wanted later, is a fresh setup, not this broken scaffold. This supersedes the D-018 note that deferred the scaffold to M8.
  7. **Demo-state hygiene** — the M8 release runs on a fresh S3 state prefix (`aircheck-m8`), leaving all M7 state under `aircheck/` intact for evidence, seeded with a small curated set of real runtime runs.
  8. **Ingress** — CORS restricted to the real public UI origin plus explicit local-development origins; interactive `/docs`+`/openapi.json` disabled by default; the previously-untagged `aircheck-http` API tagged; bounded default-stage throttling. No WAF/custom-domain/auth added.
- **Reason:** Repair the bounded release-hardening findings of the independent post-M7 audit (which returned GREEN and classified AgentCore as a real architectural component) with the narrowest reliable mechanisms, preserving every predecessor invariant.
- **Evidence:** `docs/evidence/M8/`; `aircheck/persistence/s3_lock.py`, `apps/api/service.py`, `apps/api/main.py`, `aircheck/agent/agentcore_model.py`, `deploy/deploy.py`; `tests/persistence/test_s3_lock.py`, `tests/persistence/test_concurrency.py`, `tests/agent/test_agentcore_model.py`, `tests/api/test_provenance.py`.
- **Source:** M8 Phase A mission-control authorization 2026-09-08 (Release Engineering, Hardening & Engineering Freeze), §§4–15.
