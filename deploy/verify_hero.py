"""Exercise the canonical AIRCheck hero against a DEPLOYED public endpoint.

Standard-library only (urllib) so it runs anywhere, including an ephemeral
external host (M7 non-build-machine verification). It drives the real public API:
  create Broadcast hero -> deterministic QC pauses at the Tier-2 authority boundary
  -> approve -> exact authorized derivative -> re-verify -> earned DELIVERY_READY
  -> real evidence; plus a denial -> BLOCKED path and an evidence download.
It asserts the hero invariants and prints a JSON receipt to stdout.

Usage: python deploy/verify_hero.py --base https://<host> [--out receipt.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BROADCAST = "northstar_broadcast_master_v1"


def _req(method: str, url: str, body=None, timeout: int = 90):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        ctype = resp.headers.get("content-type", "")
        parsed = json.loads(raw.decode()) if "json" in ctype else raw
        return resp.status, parsed, dict(resp.headers)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="public base URL (no trailing slash)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--label", default="deployed")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    r: dict = {"base": base, "label": args.label, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # Warm + health (deployed SHA and runtime modes).
    _, health, _ = _req("GET", f"{base}/health")
    r["health"] = health

    # Create the Broadcast hero over the faulty package.
    _, cr, _ = _req("POST", f"{base}/runs", {"profile_id": BROADCAST, "package_type": "hero"})
    run_id = cr["run_id"]
    r["create"] = {"run_id": run_id, "status": cr["status"]}

    _, d, _ = _req("GET", f"{base}/runs/{run_id}")
    r["after_create"] = {"display_status": d["display_status"], "run_state": d["run_state"]}

    _, dec, _ = _req("GET", f"{base}/runs/{run_id}/decision")
    r["decision"] = {
        "tier": dec["tier"], "tool": dec["tool"],
        "measured": dec["measured_value"], "required": dec["required_value"],
        "produces_derivative": dec.get("produces_derivative"),
    }

    _, ap_, _ = _req("POST", f"{base}/runs/{run_id}/decision", {"approved": True, "actor": "human:operator"})
    r["approve"] = {"next_status": ap_["next_status"]}

    _, f, _ = _req("GET", f"{base}/runs/{run_id}")
    tv = f.get("terminal_verdict")
    r["final"] = {
        "display_status": f["display_status"], "run_state": f["run_state"],
        "terminal": tv["outcome"] if tv else None,
        "originals_integrity_verified": tv["originals_integrity_verified"] if tv else None,
    }

    _, ev, _ = _req("GET", f"{base}/runs/{run_id}/evidence")
    r["evidence"] = {"requirements_verified": ev["requirements_verified"], "artifacts": [a["filename"] for a in ev["artifacts"]]}

    if ev["artifacts"]:
        fn = ev["artifacts"][0]["filename"]
        status, body, headers = _req("GET", f"{base}/runs/{run_id}/evidence/{fn}")
        r["evidence_download"] = {
            "filename": fn, "status": status,
            "bytes": len(body) if isinstance(body, (bytes, bytearray)) else len(json.dumps(body)),
            "content_type": headers.get("content-type") or headers.get("Content-Type"),
        }

    # Denial path -> BLOCKED (independent hero run).
    _, cr2, _ = _req("POST", f"{base}/runs", {"profile_id": BROADCAST, "package_type": "hero"})
    run2 = cr2["run_id"]
    _, dn, _ = _req("POST", f"{base}/runs/{run2}/decision", {"approved": False, "actor": "human:operator"})
    _, f2, _ = _req("GET", f"{base}/runs/{run2}")
    r["denial"] = {"run_id": run2, "next_status": dn["next_status"], "display_status": f2["display_status"]}

    _, lst, _ = _req("GET", f"{base}/runs")
    r["deliveries_count"] = len(lst["runs"])

    # Invariants.
    checks = {
        "hero_earns_delivery_ready": r["final"]["terminal"] == "DELIVERY_READY",
        "originals_integrity_verified": r["final"]["originals_integrity_verified"] is True,
        "decision_is_tier2": r["decision"]["tier"] == 2,
        "denial_blocks": r["denial"]["display_status"] == "BLOCKED",
        "evidence_downloadable": r.get("evidence_download", {}).get("status") == 200
        and r.get("evidence_download", {}).get("bytes", 0) > 0,
        "health_reports_sha": bool(health.get("git_sha")) and health.get("git_sha") != "unknown",
    }
    r["checks"] = checks
    r["result"] = "GREEN" if all(checks.values()) else "RED"

    text = json.dumps(r, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    if r["result"] != "GREEN":
        print("HERO VERIFY RED: " + ", ".join(k for k, v in checks.items() if not v), file=sys.stderr)
        return 2
    print("HERO VERIFY GREEN", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
