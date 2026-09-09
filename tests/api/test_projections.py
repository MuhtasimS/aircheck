"""M5 API projection and command contract tests.

Every assertion here is checked against the *real* deterministic M4 runtime over
the frozen synthetic universe (see ``conftest.api_client``). These tests prove
the API projects genuine runtime truth — not manufactured values — and that the
readiness invariant, human-decision flow, product-safe errors, and real evidence
downloads all hold end to end.
"""

from __future__ import annotations

from types import SimpleNamespace

from aircheck.domain.status import RunStatus

from tests.api.conftest import BROADCAST_RUN, PREVIEW_RUN


# ------------------------------------------------------------------ meta states


def test_meta_states_expose_the_canonical_vocabulary(api_client) -> None:
    body = api_client.get("/meta/states").json()
    names = {s["name"] for s in body["run_statuses"]}
    # Every lifecycle state the runtime can occupy is present.
    assert {
        "CREATED",
        "AWAITING_HUMAN_DECISION",
        "DELIVERY_READY",
        "BLOCKED",
        "FAILED",
    } <= names
    assert body["terminal_outcomes"] == ["DELIVERY_READY", "BLOCKED"]
    profiles = {p["profile_id"]: p["checks_count"] for p in body["profiles"]}
    assert profiles == {
        "northstar_broadcast_master_v1": 15,
        "northstar_digital_preview_v1": 13,
    }


# --------------------------------------------------------------- deliveries list


def test_list_runs_projects_the_two_seeded_runs(api_client) -> None:
    body = api_client.get("/runs").json()
    by_id = {r["run_id"]: r for r in body["runs"]}
    assert BROADCAST_RUN in by_id
    assert PREVIEW_RUN in by_id

    broadcast = by_id[BROADCAST_RUN]
    assert broadcast["run_state"] == "AWAITING_HUMAN_DECISION"
    assert broadcast["display_status"] == "DECISION REQUIRED"
    # No terminal verdict while awaiting a human decision.
    assert broadcast["terminal_verdict"] is None

    preview = by_id[PREVIEW_RUN]
    assert preview["run_state"] == "DELIVERY_READY"
    assert preview["display_status"] == "DELIVERY READY"
    assert preview["terminal_verdict"]["outcome"] == "DELIVERY_READY"

    counts = body["counts"]
    assert counts["decision_required"] >= 1
    assert counts["delivery_ready"] >= 1


# ------------------------------------------------------------------- run detail


def test_broadcast_detail_projects_a_real_pending_decision(api_client) -> None:
    detail = api_client.get(f"/runs/{BROADCAST_RUN}").json()
    assert detail["display_status"] == "DECISION REQUIRED"
    pending = detail["pending_decision"]
    assert pending is not None

    # Real measured fact and real destination constraint (NOT the mock's -24 ±2).
    assert pending["measured_value"].endswith("LUFS")
    assert "-26" in pending["required_value"] and "-22" in pending["required_value"]
    # Real signed distance from the real ceiling.
    assert pending["delta"] and "LUFS" in pending["delta"]
    # Real bound Tier-2 option.
    assert pending["tool"] == "create_normalized_audio_derivative"
    assert pending["tier"] == 2
    assert pending["produces_derivative"] is True
    # Real renamed derivative filename (not a fabricated program_master.mov).
    assert pending["asset_filename"].endswith(".mov")
    assert "Northstar" in pending["source_reference"]


def test_loudness_requirement_is_decision_required_not_fixed(api_client) -> None:
    """Regression: a still-failing requirement paused at a human decision must
    project as DECISION REQUIRED, never FIXED, even when a stale RESOLVED finding
    from an earlier cycle also references it. Status is anchored to the latest
    predicate outcome."""
    detail = api_client.get(f"/runs/{BROADCAST_RUN}").json()
    loudness = [
        r
        for r in detail["requirements"]
        if "loudness" in r["description"].lower()
        or r["predicate_outcome"] == "FAIL"
    ]
    assert loudness, "expected a failing loudness requirement"
    for req in loudness:
        assert req["status"] == "DECISION REQUIRED"
        assert req["status"] != "FIXED"


def test_broadcast_metrics_account_for_the_pending_decision(api_client) -> None:
    detail = api_client.get(f"/runs/{BROADCAST_RUN}").json()
    m = detail["metrics"]
    assert m["requirements_total"] == 15
    # The pending loudness decision is counted as pending, not as a fix.
    assert m["pending"] == 1
    assert m["passing"] + m["remediated"] + m["pending"] + m["failed"] == 15
    # Safe Tier-1 remediation already occurred before Tier-2 exposure.
    assert m["remediated"] >= 1


def test_detail_projects_real_append_only_lineage(api_client) -> None:
    detail = api_client.get(f"/runs/{BROADCAST_RUN}").json()
    assets = detail["assets"]
    # Both a preserved original and at least one derivative exist.
    provenance = {a["provenance"] for a in assets}
    assert any(p.startswith("ORIGINAL") for p in provenance)
    assert any("DERIVATIVE" in p for p in provenance)
    # Real events, chronologically increasing.
    seqs = [e["seq"] for e in detail["events"]]
    assert seqs == sorted(seqs)
    assert len(seqs) >= 1


# --------------------------------------------------------------------- decision


def test_decision_projection_matches_detail(api_client) -> None:
    decision = api_client.get(f"/runs/{BROADCAST_RUN}/decision").json()
    assert decision["status"] == "PENDING"
    assert decision["resolved"] is False
    assert decision["measured_value"].endswith("LUFS")
    assert "-26" in decision["required_value"]


# ---------------------------------------------------------- readiness invariant


def test_display_status_never_green_without_a_terminal_verdict(api_client) -> None:
    """The critical invariant, unit-checked on the projection helper: a
    DELIVERY_READY run *state* with a null/missing authoritative terminal verdict
    is projected as UNVERIFIED — never as green DELIVERY READY."""
    from apps.api.main import service

    snapshot = SimpleNamespace(status=RunStatus.DELIVERY_READY)
    assert service._display_status(snapshot, None) == "UNVERIFIED"

    # With a real DELIVERY_READY verdict projection, it is green.
    verdict = SimpleNamespace(outcome="DELIVERY_READY")
    assert service._display_status(snapshot, verdict) == "DELIVERY READY"


# ---------------------------------------------------------------- preview/evidence


def test_preview_is_delivery_ready_with_real_downloadable_evidence(api_client) -> None:
    detail = api_client.get(f"/runs/{PREVIEW_RUN}").json()
    assert detail["display_status"] == "DELIVERY READY"
    assert detail["terminal_verdict"]["outcome"] == "DELIVERY_READY"
    assert detail["terminal_verdict"]["originals_integrity_verified"] is True

    evidence = api_client.get(f"/runs/{PREVIEW_RUN}/evidence").json()
    assert evidence["terminal_verdict"]["outcome"] == "DELIVERY_READY"
    assert evidence["earned_at"] is not None
    assert evidence["package_hash"]
    assert evidence["artifacts"], "terminal run must expose real artifacts"

    # Every projected artifact actually exists and downloads real bytes.
    for artifact in evidence["artifacts"]:
        resp = api_client.get(artifact["download_url"])
        assert resp.status_code == 200
        assert len(resp.content) == artifact["size_bytes"] > 0


# ------------------------------------------------------------ decision commands


def _create_broadcast_run(api_client) -> str:
    resp = api_client.post(
        "/runs",
        json={"profile_id": "northstar_broadcast_master_v1", "package_type": "hero"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "AWAITING_HUMAN_DECISION"
    return body["run_id"]


def test_approved_decision_earns_delivery_ready(api_client) -> None:
    run_id = _create_broadcast_run(api_client)
    resp = api_client.post(f"/runs/{run_id}/decision", json={"approved": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["choice"] == "APPROVED"
    assert body["next_status"] == "DELIVERY_READY"

    detail = api_client.get(f"/runs/{run_id}").json()
    assert detail["display_status"] == "DELIVERY READY"
    assert detail["terminal_verdict"]["outcome"] == "DELIVERY_READY"

    evidence = api_client.get(f"/runs/{run_id}/evidence").json()
    # The human-authorized Tier-2 remediation is reflected in the metrics.
    assert evidence["metrics"]["human_authorized"] >= 1
    assert evidence["metrics"]["open_blockers"] == 0


def test_denied_decision_blocks_the_delivery(api_client) -> None:
    run_id = _create_broadcast_run(api_client)
    resp = api_client.post(f"/runs/{run_id}/decision", json={"approved": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["choice"] == "DENIED"
    assert body["next_status"] == "BLOCKED"

    detail = api_client.get(f"/runs/{run_id}").json()
    assert detail["run_state"] == "BLOCKED"
    assert detail["display_status"] == "BLOCKED"
    # A denied/blocked run is never presented as ready.
    assert detail["display_status"] != "DELIVERY READY"


# ---------------------------------------------------------- product-safe errors


def test_missing_run_returns_product_safe_404(api_client) -> None:
    for suffix in ("", "/decision", "/evidence"):
        resp = api_client.get(f"/runs/run_does_not_exist{suffix}")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        # No internal path/traceback leakage.
        assert "Traceback" not in detail
        assert "\\" not in detail and "/store" not in detail


def test_decision_on_a_run_without_pending_decision_is_404(api_client) -> None:
    resp = api_client.get(f"/runs/{PREVIEW_RUN}/decision")
    assert resp.status_code == 404


def test_submitting_a_decision_when_none_is_pending_is_400(api_client) -> None:
    resp = api_client.post(f"/runs/{PREVIEW_RUN}/decision", json={"approved": True})
    assert resp.status_code == 400


def test_evidence_download_rejects_missing_and_traversal(api_client) -> None:
    assert api_client.get(f"/runs/{PREVIEW_RUN}/evidence/missing.json").status_code == 404
    # Path traversal must never resolve outside the evidence directory.
    traversal = api_client.get(f"/runs/{PREVIEW_RUN}/evidence/..%2f..%2fsecret")
    assert traversal.status_code in (400, 404)
