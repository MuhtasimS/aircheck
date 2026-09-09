"""AIRCheck bounded local demo — deterministic, provider-free, no AWS.

Drives the real M4 deterministic runtime through the M5 application service over
the frozen synthetic universe ("The Last Lightkeeper"), with no network, no cloud,
and no model provider (the deterministic semantic double stands in for AgentCore).
It exercises the canonical hero and a denial and prints a truthful summary:

  hero    : create -> Tier-2 human decision -> approve -> DELIVERY_READY (15/15)
  denial  : create -> deny -> BLOCKED

Exit code 0 on success, non-zero if any invariant is not met. Requires a local
``ffmpeg``/``ffprobe`` (deterministic media truth) — the only external dependency.
State is written to a throwaway temp directory, never into the repository.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path


def _isolated_env(root: Path) -> None:
    # Force a hermetic local run: no S3, no AgentCore, temp roots regardless of any
    # AIRCHECK_* the developer already has exported.
    for var in ("AIRCHECK_S3_BUCKET", "AIRCHECK_S3_PREFIX", "AIRCHECK_AGENTCORE_RUNTIME_ARN"):
        os.environ.pop(var, None)
    os.environ["AIRCHECK_STORE_ROOT"] = str(root / "store")
    os.environ["AIRCHECK_WORKSPACE_ROOT"] = str(root / "workspace")
    os.environ["AIRCHECK_UNIVERSE_ROOT"] = str(root / "universe")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="aircheck-demo-"))
    _isolated_env(root)

    # Imported after env is set so the module-level service (if any) is unaffected;
    # here we construct our own service instance explicitly.
    from apps.api.models import CreateRunRequest
    from apps.api.service import AIRCheckService
    from aircheck.domain.status import RunStatus

    svc = AIRCheckService(
        store_root=root / "store",
        workspace_root=root / "workspace",
        universe_root=root / "universe",
    )
    profile = "northstar_broadcast_master_v1"
    ok = True

    print("AIRCheck bounded local demo (deterministic, provider-free, no AWS)")
    print("=" * 68)
    print("Program: The Last Lightkeeper   Profile: Northstar Broadcast Master\n")

    try:
        # --- Hero: create -> human decision -> approve -> DELIVERY_READY ----------
        print("[hero] creating a Broadcast Master run...")
        created = svc.create_run(CreateRunRequest(profile_id=profile, package_type="hero"))
        print(f"       run_id={created.run_id}  status={created.status}")
        ok &= created.status == RunStatus.AWAITING_HUMAN_DECISION.value

        decision = svc.get_decision(created.run_id)
        print(f"[hero] human decision required (Tier {decision.tier}):")
        print(f"       operation : {decision.tool}")
        print(f"       measured  : {decision.measured_value}")
        print(f"       required  : {decision.required_value}")
        print(f"       original  : {decision.original_preservation}")

        print("[hero] approving...")
        decided = svc.submit_decision(created.run_id, approved=True)
        print(f"       next_status={decided.next_status}")
        ok &= decided.next_status == RunStatus.DELIVERY_READY.value

        evidence = svc.get_evidence(created.run_id)
        tv = evidence.terminal_verdict
        integrity = tv.originals_integrity_verified if tv is not None else False
        print(f"[hero] terminal={evidence.run_state}  {evidence.requirements_verified}")
        print(f"       originals_integrity_verified={integrity}")
        print(f"       evidence artifacts: {[a.filename for a in evidence.artifacts]}")
        ok &= evidence.run_state == RunStatus.DELIVERY_READY.value
        ok &= integrity is True
        ok &= len(evidence.artifacts) > 0

        # --- Denial: create -> deny -> BLOCKED -----------------------------------
        print("\n[denial] creating a second Broadcast Master run...")
        denied_run = svc.create_run(CreateRunRequest(profile_id=profile, package_type="hero"))
        print(f"         run_id={denied_run.run_id}  status={denied_run.status}")
        denied = svc.submit_decision(denied_run.run_id, approved=False)
        print(f"[denial] denied -> next_status={denied.next_status}")
        ok &= denied.next_status == RunStatus.BLOCKED.value

        print("\n" + "=" * 68)
        print(f"DEMO RESULT: {'GREEN - all invariants held' if ok else 'RED - an invariant failed'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
