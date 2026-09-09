"""Render RESULTS.md deterministically from the machine-readable corpus results.

The table and every aggregate are produced from the harness output, never edited
by hand. Reliability numbers mix only comparable stages; there is no single
vanity percentage.
"""

from __future__ import annotations

from typing import Any


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _note(record: dict[str, Any]) -> str:
    if record["eval_result"] == "PASS":
        return "matches frozen expected at every graded stage"
    if record["eval_result"] == "ERROR":
        return f"harness error: {record.get('error_detail', '')}"
    fields = record.get("divergence_fields") or []
    return f"diverged at `{record.get('first_divergence')}` on {', '.join(fields) or 'terminal'}"


def render_markdown(results: dict[str, Any]) -> str:
    totals = results["totals"]
    lines: list[str] = []
    lines.append("# AIRCheck M6 — Evaluation Corpus Results")
    lines.append("")
    lines.append(
        "Deterministic end-to-end evaluation of the frozen M4 runtime. Each scenario "
        "declares a complete, scenario-appropriate set of typed expected stages; every "
        "declared stage is graded by exact deterministic comparison of its declared "
        "fields AND declared material relationships: requirement/plan/finding maps by "
        "key; per-cycle measurement records (value/unit/status/error); per-cycle "
        "predicates with their predicate->measurement provenance binding (normalized to "
        "measurement key + requirement identity + cycle); and the ordered lifecycle+action "
        "event ledger graded as coherent typed events (envelope<->payload agreement and "
        "completion/failure->start linkage via stable per-run ordinals) — no subset or "
        "extra-key evasion. The guarantee is bounded to these graded fields and relationships. "
        "There is **no LLM judge**: the stochastic model is measured (predecessor M3 "
        "evidence), never trusted to grade itself. This corpus is the third post-audit "
        "repair: the original close and the first two repairs were each invalidated by "
        "an independent RED audit (`audit/M6_INDEPENDENT_AUDIT_RED.md`, "
        "`audit/M6_INDEPENDENT_AUDIT_2_RED.md`, `audit/M6_INDEPENDENT_AUDIT_3_RED.md`); "
        "the full arc is in `M6_REPAIR_3_REPORT.md`."
    )
    lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- Corpus version: `{results['corpus_version']}`")
    lines.append(f"- Git commit: `{results['git_sha']}` (dirty: {str(results['git_dirty']).lower()})")
    lines.append(f"- Generated at: {results['generated_at']}")
    env = results["environment"]
    lines.append(f"- Python: {env['python']} · Platform: {env['platform']}")
    lines.append(f"- Media toolchain: {env['ffmpeg']}")
    lines.append(f"- Grading: {env['grading']}")
    lines.append(f"- Semantic boundary: {env['semantic_boundary']}")
    lines.append(
        f"- Model/provider runs: {env['provider_runs']} · Deterministic runs: {env['deterministic_runs']}"
    )
    lines.append(f"- Stochastic semantic evidence: {env['stochastic_semantic_evidence']}")
    lines.append("")

    lines.append("## Aggregate reliability")
    lines.append("")
    lines.append(f"- Scenarios frozen and executed: **{totals['scenarios']}**")
    lines.append(
        f"- Eval passes: **{totals['passed']}/{totals['scenarios']}** · "
        f"failures: **{totals['failed']}** · harness errors: **{totals['errored']}**"
    )
    lines.append(
        f"- Runtime-terminal matches (`DELIVERY_READY`/`BLOCKED`): "
        f"**{totals['runtime_terminal_matched']}/{totals['runtime_terminal_total']}**"
    )
    lines.append(
        f"- Safe refusals (`REFUSED_SAFE` — an eval/safety classification, not a runtime "
        f"terminal state): **{totals['safe_refusal_matched']}/{totals['safe_refusal_total']}**"
    )
    lines.append(
        f"- Model/provider runs: **{totals['provider_runs']}** · "
        f"Deterministic runs: **{totals['deterministic_runs']}**"
    )
    lines.append("")
    lines.append(
        "These are separate, comparable metrics, not a blended percentage. The architecture "
        "defines only `DELIVERY_READY`, `BLOCKED`, and `FAILED` as runtime terminal states; "
        "`REFUSED_SAFE` is a corpus classification for a run the runtime correctly refused to "
        "advance (a rejected model self-authorization) with no durable side effect, so it is "
        "reported separately from runtime-terminal matches. Per-stage reliability (every "
        "declared stage matches frozen truth) is the eval pass count above."
    )
    lines.append("")

    lines.append("## Results")
    lines.append("")
    lines.append(_row(["Scenario", "Expected terminal", "Actual terminal", "Eval result", "First divergence", "Notes"]))
    lines.append(_row(["---", "---", "---", "---", "---", "---"]))
    for record in results["scenarios"]:
        divergence = record.get("first_divergence") or "—"
        lines.append(
            _row(
                [
                    f"`{record['scenario_id']}`",
                    record.get("expected_terminal", "—"),
                    str(record.get("actual_terminal") or "—"),
                    record["eval_result"],
                    f"`{divergence}`" if divergence != "—" else "—",
                    _note(record),
                ]
            )
        )
    lines.append("")

    lines.append("## First-divergence counts by stage")
    lines.append("")
    counts = results["first_divergence_by_stage"]
    if counts:
        for stage in sorted(counts):
            lines.append(f"- `{stage}`: {counts[stage]}")
    else:
        lines.append("- No scenario diverged from frozen expected truth at any stage.")
    lines.append("")

    lines.append("## Failure modes")
    lines.append("")
    failures = [r for r in results["scenarios"] if r["eval_result"] in {"FAIL", "ERROR"}]
    if not failures:
        lines.append(
            "No scenario diverged from frozen expected truth in this run. A strong result "
            "does not require perfection; had a scenario failed, this section would record "
            "its first divergent stage, the observed behavior, whether the deterministic "
            "safeguards still forced a safe terminal state, and whether the cause was an "
            "implementation defect or provider/stochastic variation. Each scenario is graded "
            "end to end against typed truth frozen before the authoritative rerun, so a "
            "regression in any graded field of a declared stage — including a deleted or "
            "altered per-cycle measurement or a missing/reordered lifecycle transition — "
            "surfaces as a divergence at its earliest stage rather than hiding behind a safe "
            "final outcome. The guarantee is bounded to the fields actually graded (listed "
            "above); it is not a claim about ungraded aspects of the runtime."
        )
    else:
        for record in failures:
            lines.append(f"### `{record['scenario_id']}` — {record['title']}")
            lines.append("")
            lines.append(f"- Eval result: **{record['eval_result']}**")
            lines.append(
                f"- Expected terminal `{record.get('expected_terminal')}`, "
                f"actual `{record.get('actual_terminal')}`"
            )
            if record.get("first_divergence"):
                lines.append(f"- First divergence: `{record['first_divergence']}`")
            summary = record.get("difference_summary", {})
            if summary.get("fields"):
                for field, diff in summary["fields"].items():
                    lines.append(
                        f"  - `{field}`: expected `{diff['expected']}`, observed `{diff['observed']}`"
                    )
            if record.get("error_detail"):
                lines.append(f"- Detail: {record['error_detail']}")
            lines.append("")

    lines.append("## Safety invariants exercised")
    lines.append("")
    lines.append(
        "- The model never establishes media facts, predicate results, or terminal verdicts; "
        "deterministic tools and `terminal.compute()` do."
    )
    lines.append(
        "- Tier-2 content-affecting derivatives require an explicit human decision and a "
        "runtime-minted single-use authorization; denial routes to `BLOCKED` "
        "(`15_denied_approval_blocks`)."
    )
    lines.append(
        "- A model attempting to self-authorize a Tier-2 action is rejected with no side "
        "effect (`16_model_self_authorization_refused`)."
    )
    lines.append(
        "- Unfixable technical defects and integrity failures fail closed to `BLOCKED` "
        "(`17`–`22`); bounded remediation failure and loop-limit exhaustion also block "
        "(`23`, `24`)."
    )
    lines.append(
        "- Originals are re-hashed at terminal and never mutated; evidence artifacts are "
        "hash-verified."
    )
    lines.append("")

    lines.append("## Methodology, credibility, and limitations")
    lines.append("")
    lines.append(
        "- **Why a fully-passing deterministic corpus is the correct result.** These scenarios "
        "grade the deterministic runtime (unchanged from gate/M5) driven by a typed semantic "
        "double. For a correct deterministic system, one identical input must yield one expected "
        "result; a deterministic stage that varied would be a defect, not acceptable variance. "
        "Expected truth is a golden snapshot of that runtime, frozen before the authoritative "
        "rerun, with canonical facts cross-checked against `m2_product_profiles.json`, the closed "
        "catalog, `evals/expected/`, and the M4 hero receipt."
    )
    lines.append(
        "- **The green is falsifiable (the evaluator was hardened, not the numbers).** "
        "`tests/evals/test_corpus_harness.py` seeds each corruption class the three independent "
        "audits demonstrated and proves it is caught at its earliest stage. Node corruption: a "
        "wrong predicate-key map (predicate); a deleted/changed/wrong-unit/wrong-cycle measurement "
        "(measurement); a nonsensical recovery count (recovery); a mis-bound plan item (planning); "
        "an earlier-cycle predicate overwrite (predicate); a deleted/reordered/duplicated lifecycle "
        "transition (events); a wrong requirement severity/constraint (admission); an extra-key "
        "evasion. Relationship corruption: a predicate rebound to another requirement's measurement, "
        "to a wrong-cycle measurement, or to a missing measurement (predicate); an event whose "
        "envelope type contradicts its payload, an action completion/failure rebound to a different "
        "start, a completion without a start, or a tool-identity mismatch (events). Expected-artifact "
        "validation rejects removing a required material field (`plan_items`, `final_by_key`, "
        "`fail_keys_by_cycle`, `bindings`, measurement `records`, event `skeleton`, "
        "`admission.status_reasons`, ...). A seeded admission+terminal mismatch attributes to "
        "`admission`, and a seeded FAIL/ERROR drives the corpus gate command non-zero and closure RED."
    )
    lines.append(
        "- **Conditional applicability — a documented v1 capability gap, not equivalent coverage.** "
        "AIRCheck v1 does not implement runtime `content_type` applicability resolution and never "
        "produces `NOT_APPLICABLE`; admission validates a condition's grammar/polarity and marks a "
        "requirement APPLICABLE (the check then always runs) or UNRESOLVED. Scenarios `25`/`26` "
        "exercise the real grammar (a supported `NEQ` polarity admitted APPLICABLE; a reversed "
        "polarity downgraded to UNRESOLVED via `APPLICABILITY_POLARITY_UNPROVEN`), and `13` covers "
        "an unsupported applicability field. The canonical corpus's applicable/non-applicable "
        "`content_type` polarity branch (`CONSOLIDATED_PLAN.md` §G.3 scenario 6, which requires "
        "runtime `NOT_APPLICABLE`) is **not implemented in v1** and is recorded here as a capability "
        "gap rather than executed or relabelled (mission-control decision; see `DECISIONS.md` D-015)."
    )
    lines.append(
        "- **Stochastic reliability is out of scope here and lives in M3.** Whether a live model "
        "produces useful typed candidates was measured under M3 (Gemini 2.5 Flash: 3/3 useful "
        "batches per profile plus seven adversarial outcomes, `docs/evidence/M3/`). This corpus "
        "deliberately holds the semantic layer fixed to isolate deterministic reliability; it does "
        "not re-measure provider variance and records zero provider runs."
    )
    lines.append(
        "- **Corpus finding — integer frame rates are unrepresentable.** The frame-rate defect "
        "fixture must use a non-integer rate (29.97 = 30000/1001): the RATIONAL measurement is "
        "canonical only when `Fraction(value)` round-trips, so an integer rate like 30 (which "
        "reduces to `30`) cannot be recorded. This is a pre-existing property of the closed "
        "catalog surfaced by the corpus, not an M6 change; it is noted for a future gate."
    )
    lines.append(
        "- **Injected faults are explicit.** Two recovery scenarios use documented fault injection "
        "(`23` a failing temporary write; `24` a remediation that never resolves the defect) to "
        "reach the deterministic fail-closed paths; both are marked in the harness and this report."
    )
    lines.append("")

    lines.append("## Mission scenario coverage")
    lines.append("")
    lines.append(_row(["Scenario", "Mission requirement"]))
    lines.append(_row(["---", "---"]))
    for record in results["scenarios"]:
        lines.append(_row([f"`{record['scenario_id']}`", record["mission_ref"]]))
    lines.append("")

    lines.append("## Ground-truth integrity")
    lines.append("")
    lines.append(
        "Each scenario's typed expected artifact lives under `evals/corpus/expected/`. For this "
        "post-audit repair the expected artifacts were **immutably frozen before the authoritative "
        "rerun**, bound to a dedicated freeze commit whose hash and per-artifact hashes this "
        "results file records under `expected_snapshot`. The precise claim is that the recorded "
        "expected hashes were committed before the authoritative rerun that produced these "
        "results — not a claim about any earlier execution. Expected truth is a golden snapshot of "
        "the deterministic runtime (unchanged from gate/M5), with canonical facts cross-checked "
        "against `specifications/northstar/profiles/m2_product_profiles.json`, the closed catalog, "
        "and the inherited M2/M3 adversarial ground truth under `evals/expected/` (reused "
        "unchanged). The result is meaningful as fixed-double deterministic conformance and "
        "regression protection, not live-model semantic reliability — that remains predecessor M3 "
        "evidence, which explicitly did not evaluate terminal behavior."
    )
    return "\n".join(lines).rstrip("\n") + "\n"


__all__ = ("render_markdown",)
