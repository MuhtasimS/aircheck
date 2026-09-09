"""Minimal compatibility probe: the FastAPI stack runs warning-clean.

The full backend suite runs under ``filterwarnings = ["error"]``; this asserts
the FastAPI/Starlette/httpx TestClient path raises no warning-as-error and that
the health and meta endpoints respond.
"""

from __future__ import annotations


def test_health(api_client) -> None:
    resp = api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_meta_states(api_client) -> None:
    resp = api_client.get("/meta/states")
    assert resp.status_code == 200
    body = resp.json()
    assert body["terminal_outcomes"] == ["DELIVERY_READY", "BLOCKED"]
    assert {p["profile_id"] for p in body["profiles"]} == {
        "northstar_broadcast_master_v1",
        "northstar_digital_preview_v1",
    }
