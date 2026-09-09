"""Deterministic M6 closure audit.

Proves the gate invariants before the M6 gate-closing commit: exact predecessor,
M6-only scope, untouched runtime/UI/canonical docs/predecessor evidence and
inherited ground truth, that expected artifacts were frozen before the post-audit
authoritative rerun and were not rewritten to match observed output, that every result
row came from harness output, that there is no LLM judge, and that no secret or
private reasoning leaked into the receipts.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path

from evals.run_corpus import exit_code_for

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "evidence" / "M6"
EXPECTED_DIR = ROOT / "evals" / "corpus" / "expected"
EXPECTED_M5_SHA = "1409ce498de358c998db12dd0ffc4733569f16fa"
# Ordinal immutable supersession tags (D-016/D-017); each preserves a superseded
# close and is never moved. gate/M6 always points to the current GREEN close.
SUPERSEDED_TAGS = {
    "gate/M6-superseded": "41ed40cb2b8ab001b591d513f1ce821f8a74fe58",
    "gate/M6-superseded-2": "352c3b670eccfb40171865461dfd07cfabe0c267",
    "gate/M6-superseded-3": "8543e00e4e0682096913fd51026f61044956d2d5",
}


def closure_ok(results: dict) -> tuple[bool, str]:
    """M6 may only close when every authorized scenario passed with no errors."""

    totals = results["totals"]
    reasons: list[str] = []
    if totals["passed"] != totals["scenarios"]:
        reasons.append(f"passed {totals['passed']} != scenarios {totals['scenarios']}")
    if totals["failed"] != 0:
        reasons.append(f"failed {totals['failed']} != 0")
    if totals["errored"] != 0:
        reasons.append(f"errored {totals['errored']} != 0")
    if totals["passed"] + totals["failed"] + totals["errored"] != totals["scenarios"]:
        reasons.append("classification totals do not reconcile")
    if exit_code_for(results) != 0:
        reasons.append(f"corpus gate exit code {exit_code_for(results)} != 0")
    return (not reasons, "; ".join(reasons) if reasons else "all scenarios passed; totals reconcile")

PREDECESSOR_EVIDENCE = (
    "docs/evidence/M1", "docs/evidence/M2", "docs/evidence/M3", "docs/evidence/M3a",
    "docs/evidence/M4", "docs/evidence/M5", "docs/evidence/R1", "docs/evidence/R2",
)
# DECISIONS.md is excluded here because mission control authorized appending
# D-015/D-016; it is checked separately as append-only (no deletions vs gate/M5).
CANONICAL_DOCS = (
    "ARCHITECTURE.md", "PRODUCT_CONTRACT.md", "docs/WORKER_CONTRACT.md",
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=False, capture_output=True, text=True
    ).stdout.strip()


def _diff_empty(paths: tuple[str, ...]) -> tuple[bool, str]:
    stat = _git("diff", "--stat", "gate/M5", "--", *paths)
    return (stat == "", stat)


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def audit() -> tuple[list[tuple[str, bool, str]], bool]:
    checks: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    resolved = _git("rev-list", "-n", "1", "gate/M5")
    record("predecessor gate/M5 exact", resolved == EXPECTED_M5_SHA, resolved)

    for label, paths in (
        ("runtime aircheck/ unchanged vs gate/M5", ("aircheck",)),
        ("M5 UI/API apps/ unchanged vs gate/M5", ("apps",)),
        ("canonical docs unchanged vs gate/M5", CANONICAL_DOCS),
        ("predecessor evidence untouched", PREDECESSOR_EVIDENCE),
        ("inherited evals/expected untouched", ("evals/expected",)),
        ("M3 eval runner untouched", ("evals/runners",)),
    ):
        ok, stat = _diff_empty(paths)
        record(label, ok, stat.replace("\n", " | ") if stat else "empty diff")

    # DECISIONS.md may only grow (authorized D-015/D-016 appended); no deletions.
    numstat = _git("diff", "--numstat", "gate/M5", "--", "DECISIONS.md")
    deletions = numstat.split("\t")[1] if numstat and "\t" in numstat else "0"
    record(
        "DECISIONS.md is append-only vs gate/M5 (no deletions)",
        deletions in {"0", ""},
        numstat or "no change",
    )

    # Freeze predates the first corpus run.
    freeze = json.loads((EVIDENCE / "M6.1_corpus_freeze.json").read_text(encoding="utf-8"))
    record(
        "expected frozen and schema-valid before run",
        freeze["all_frozen_and_valid"] is True and freeze["prior_results_present"] is False,
        f"all_valid={freeze['all_frozen_and_valid']} prior_results={freeze['prior_results_present']}",
    )

    # Ground truth was not rewritten to match observed output: the hash recorded
    # in the freeze receipt, the hash referenced by each result row, and the hash
    # of the current expected file on disk must all agree.
    results = json.loads((EVIDENCE / "results.json").read_text(encoding="utf-8"))
    freeze_hashes = {e["scenario_id"]: e.get("sha256") for e in freeze["scenarios"]}
    result_rows = {r["scenario_id"]: r for r in results["scenarios"]}
    hash_ok = True
    hash_detail: list[str] = []
    for path in sorted(EXPECTED_DIR.glob("*.json")):
        sid = path.stem
        disk = _sha256_file(path)
        frozen = freeze_hashes.get(sid)
        used = result_rows.get(sid, {}).get("expected_artifact_sha256")
        if not (disk == frozen == used):
            hash_ok = False
            hash_detail.append(f"{sid}: disk={disk[:8]} frozen={str(frozen)[:8]} used={str(used)[:8]}")
    record(
        "expected artifacts frozen == used == on-disk (not rewritten)",
        hash_ok,
        "; ".join(hash_detail) if hash_detail else f"all {len(freeze_hashes)} hashes agree",
    )

    # Every authorized scenario is represented and every row came from harness output.
    from evals.corpus.scenarios import scenario_ids

    ids_expected = set(scenario_ids())
    ids_results = {r["scenario_id"] for r in results["scenarios"]}
    record(
        "all authorized scenarios represented",
        ids_expected == ids_results and len(ids_results) == len(ids_expected),
        f"expected={len(ids_expected)} results={len(ids_results)}",
    )
    ok_close, close_detail = closure_ok(results)
    record(
        "corpus closes GREEN (passed==scenarios, 0 failed, 0 errored, exit 0)",
        ok_close,
        close_detail,
    )
    structural = all(
        "expected_artifact_sha256" in r and "first_divergence" in r and "eval_result" in r
        for r in results["scenarios"]
    )
    record("every result row carries harness-computed fields", structural)

    # The canonical verification path executes the actual corpus gate command and
    # requires a falsifying exit code (0 only when every scenario passes).
    with tempfile.TemporaryDirectory(prefix="m6-closure-") as tmp:
        completed = subprocess.run(
            [sys.executable, "-m", "evals.run_corpus", "--results-dir", tmp],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        fresh_ok = True
        fresh_detail = f"exit={completed.returncode}"
        if completed.returncode != 0:
            fresh_ok = False
        else:
            fresh_results_path = Path(tmp) / "results.json"
            if not fresh_results_path.exists():
                fresh_ok = False
                fresh_detail += " (no results produced)"
            else:
                fresh = json.loads(fresh_results_path.read_text(encoding="utf-8"))
                fresh_close, fresh_close_detail = closure_ok(fresh)
                fresh_ok = fresh_close
                fresh_detail += f" | {fresh_close_detail}"
    record(
        "corpus gate command executes and exits 0 with all scenarios passing",
        fresh_ok,
        fresh_detail,
    )

    for tag, sha in SUPERSEDED_TAGS.items():
        resolved_tag = _git("rev-list", "-n", "1", tag)
        record(
            f"{tag} anchored at its superseded false-green commit",
            resolved_tag == sha,
            resolved_tag or f"{tag} missing",
        )
    audit_receipt = EVIDENCE / "audit" / "M6_INDEPENDENT_AUDIT_RED.md"
    record("independent RED audit preserved as a durable receipt", audit_receipt.is_file())

    # No LLM judge: deterministic grading, zero provider runs, and no provider
    # import anywhere on the corpus grading path.
    env = results["environment"]
    grading_ok = "no LLM judge" in env["grading"] and env["provider_runs"] == 0
    # The grading path only: batches, model, stages, render, scenarios, harness.
    # This audit module is excluded (it names provider patterns to detect them).
    corpus_src = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((ROOT / "evals" / "corpus").glob("*.py"))
        if p.name != "closure_audit.py"
    ) + (ROOT / "evals" / "run_corpus.py").read_text(encoding="utf-8")
    no_provider_import = not re.search(r"^\s*(from|import)\s+.*provider", corpus_src, re.MULTILINE)
    no_live_model = not re.search(r"Gemini\w*Model|BedrockModel", corpus_src)
    record(
        "no LLM-as-judge (deterministic grading, 0 provider runs, no live model)",
        grading_ok and no_provider_import and no_live_model,
        f"grading_ok={grading_ok} no_provider_import={no_provider_import} no_live_model={no_live_model}",
    )

    # Documented failure-mode section remains visible even when all pass.
    results_md = (EVIDENCE / "RESULTS.md").read_text(encoding="utf-8")
    record(
        "RESULTS.md contains an honest failure-mode section",
        "## Failure modes" in results_md,
    )

    # No secrets or private reasoning or local absolute paths in the receipts.
    secret_patterns = (
        r"auth_[A-Za-z0-9_-]{6,}",
        r"AKIA[0-9A-Z]{16}",
        r"BEGIN [A-Z ]*PRIVATE KEY",
        r"api[_-]?key\s*[:=]",
        r"[A-Za-z]:\\\\Users\\\\",
        r"/home/[a-z]",
        r"reasoningSignature",
    )
    leaks: list[str] = []
    for path in sorted(EVIDENCE.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in secret_patterns:
            if re.search(pattern, text):
                leaks.append(f"{path.name}:{pattern}")
    record("no secrets/private-reasoning/local-path leakage in receipts", not leaks, "; ".join(leaks))

    ok = all(passed for _, passed, _ in checks)
    return checks, ok


def main() -> int:
    checks, ok = audit()
    lines = ["AIRCheck M6 closure audit", ""]
    for name, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        lines.append(f"[{status}] {name}" + (f" - {detail}" if detail else ""))
    lines.append("")
    lines.append(f"RESULT: {'GREEN' if ok else 'RED'}")
    text = "\n".join(lines) + "\n"
    (EVIDENCE / "M6.5_closure_audit.txt").write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
