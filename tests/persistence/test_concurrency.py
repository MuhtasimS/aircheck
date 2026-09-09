"""M8 cross-container mutation-safety regression (audit P1).

The M7 independent audit reproduced a real flaw: two warm Lambda containers
concurrently submitted APPROVE and DENY against the same pending decision and
BOTH returned success, with last-writer-wins application-visible state. These
tests reproduce that exact scenario against the real deterministic runtime with
two ``AIRCheckService`` instances (separate caches, one shared in-memory S3) and
prove the per-run S3 lease repairs it: exactly one conflicting mutation wins, the
loser conflicts (surfaced as HTTP 409), no second Tier-2 derivative or duplicate
authorization is minted, and a stale writer never overwrites newer authoritative
state.

Hermetic and provider-free: the shared fake S3 enforces the same conditional-write
preconditions as real S3 (verified live), and the semantic step runs on the
deterministic double (no AgentCore configured).
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from aircheck.domain.status import RunStatus
from aircheck.persistence.s3_lock import ConflictError
from apps.api.models import CreateRunRequest
from apps.api.service import AIRCheckService
from synthetic.generator.last_lightkeeper import build_universe

BUCKET = "aircheck-test-state"
BROADCAST = "northstar_broadcast_master_v1"


class _Paginator:
    def __init__(self, store: "SharedS3") -> None:
        self._store = store

    def paginate(self, Bucket, Prefix=""):
        yield {
            "Contents": [
                {"Key": k, "Size": len(v), "ETag": '"' + hashlib.md5(v).hexdigest() + '"'}
                for (b, k), v in sorted(self._store.objects.items())
                if b == Bucket and k.startswith(Prefix)
            ]
        }


class SharedS3:
    """One in-memory S3 shared by two containers: mirror + conditional-lock surface."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.downloaded_keys: list[str] = []

    # --- mirror surface (S3StateMirror uses positional upload/download) ---
    def upload_file(self, filename, bucket, key):
        self.objects[(bucket, key)] = Path(filename).read_bytes()

    def download_file(self, bucket, key, filename):
        dest = Path(filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.objects[(bucket, key)])
        self.downloaded_keys.append(key)

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _Paginator(self)

    # --- conditional-write surface (S3RunLease uses keyword put/get/delete) ---
    @staticmethod
    def _etag(data: bytes) -> str:
        return '"' + hashlib.md5(data).hexdigest() + '"'

    @staticmethod
    def _fail(code, op) -> ClientError:
        return ClientError({"Error": {"Code": code, "Message": code}}, op)

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch=None, IfMatch=None):
        key = (Bucket, Key)
        exists = key in self.objects
        if IfNoneMatch == "*" and exists:
            raise self._fail("PreconditionFailed", "PutObject")
        if IfMatch is not None and (not exists or self._etag(self.objects[key]) != IfMatch):
            raise self._fail("PreconditionFailed", "PutObject")
        self.objects[key] = bytes(Body) if isinstance(Body, (bytes, bytearray)) else str(Body).encode()
        return {"ETag": self._etag(self.objects[key])}

    def get_object(self, *, Bucket, Key):
        key = (Bucket, Key)
        if key not in self.objects:
            raise self._fail("NoSuchKey", "GetObject")
        data = self.objects[key]
        return {"ETag": self._etag(data), "Body": io.BytesIO(data)}

    def delete_object(self, *, Bucket, Key, IfMatch=None):
        key = (Bucket, Key)
        if IfMatch is not None and (key not in self.objects or self._etag(self.objects[key]) != IfMatch):
            raise self._fail("PreconditionFailed", "DeleteObject")
        self.objects.pop(key, None)
        return {}


@pytest.fixture(scope="module")
def universe_dir(tmp_path_factory) -> Path:
    # build_universe requires an empty, not-yet-existing destination.
    dest = tmp_path_factory.mktemp("universe") / "u"
    build_universe(dest)
    return dest


def _service(tmp_path, sub, universe_dir, s3) -> AIRCheckService:
    base = tmp_path / sub
    return AIRCheckService(
        store_root=base / "store",
        workspace_root=base / "workspace",
        universe_root=universe_dir,
        s3_client=s3,
    )


@pytest.fixture
def two_containers(tmp_path, universe_dir, monkeypatch):
    """Two services sharing one S3, with a fresh Broadcast run paused at Tier-2.

    Returns (container_a, container_b, run_id). Both containers have hydrated the
    awaiting run, mirroring two warm Lambdas that each observed the pending decision.
    """
    monkeypatch.setenv("AIRCHECK_S3_BUCKET", BUCKET)
    monkeypatch.setenv("AIRCHECK_S3_PREFIX", "aircheck")
    for var in ("AIRCHECK_STORE_ROOT", "AIRCHECK_WORKSPACE_ROOT", "AIRCHECK_UNIVERSE_ROOT",
                "AIRCHECK_AGENTCORE_RUNTIME_ARN"):
        monkeypatch.delenv(var, raising=False)

    s3 = SharedS3()
    a = _service(tmp_path, "a", universe_dir, s3)
    b = _service(tmp_path, "b", universe_dir, s3)

    created = a.create_run(CreateRunRequest(profile_id=BROADCAST, package_type="hero"))
    assert created.status == RunStatus.AWAITING_HUMAN_DECISION.value
    run_id = created.run_id

    # Container B is a separate warm container: it hydrates the same pending run.
    b.sync_index()
    b.hydrate_run(run_id)
    assert b.store.load_snapshot(run_id).status is RunStatus.AWAITING_HUMAN_DECISION
    return a, b, run_id


def test_concurrent_approve_and_deny_one_wins_one_conflicts(two_containers):
    a, b, run_id = two_containers
    # A approves and wins.
    res_a = a.submit_decision(run_id, approved=True)
    assert res_a.next_status == RunStatus.DELIVERY_READY.value
    # B still holds the stale "awaiting" view and tries to DENY -> truthful conflict.
    with pytest.raises(ConflictError):
        b.submit_decision(run_id, approved=False)
    # Authoritative durable state is the winner's, unchanged by the loser.
    b.sync_index()
    assert b.store.load_snapshot(run_id).status is RunStatus.DELIVERY_READY


def test_concurrent_double_approve_mints_one_authorization(two_containers):
    a, b, run_id = two_containers
    a.submit_decision(run_id, approved=True)
    with pytest.raises(ConflictError):
        b.submit_decision(run_id, approved=True)
    # Exactly one human-authorized Tier-2 remediation exists (no duplicate derivative).
    run = a.store.load_runtime_run(run_id)
    assert run.authorized_remediations == 1
    derivatives = [ra for ra in run.assets if ra.relative_path.startswith("derivatives/")]
    assert len(derivatives) == 1


def test_stale_writer_cannot_overwrite_newer_snapshot(two_containers):
    a, b, run_id = two_containers
    # A denies first -> BLOCKED terminal.
    a.submit_decision(run_id, approved=False)
    assert a.store.load_snapshot(run_id).status is RunStatus.BLOCKED
    # B's stale approve must not resurrect the run past a terminal denial.
    with pytest.raises(ConflictError):
        b.submit_decision(run_id, approved=True)
    b.sync_index()
    assert b.store.load_snapshot(run_id).status is RunStatus.BLOCKED


def test_conflict_leaves_no_new_derivative_or_evidence(two_containers):
    a, b, run_id = two_containers
    a.submit_decision(run_id, approved=False)  # BLOCKED, no derivative produced
    before = dict(_snapshot_of(b_shared(a)))
    with pytest.raises(ConflictError):
        b.submit_decision(run_id, approved=True)
    after = dict(_snapshot_of(b_shared(a)))
    # The loser wrote nothing to durable S3: the object set is byte-identical.
    assert before == after


def test_retry_after_observing_fresh_state_is_truthful(two_containers):
    a, b, run_id = two_containers
    a.submit_decision(run_id, approved=True)
    # B's next request refreshes the index (as the middleware does) and now sees the
    # resolved run: a re-submit is a truthful "not awaiting" reject, not a 409 race.
    b.sync_index()
    with pytest.raises(ValueError):
        b.submit_decision(run_id, approved=False)


def test_cold_container_hydrates_index_then_only_the_accessed_run(
    tmp_path, universe_dir, monkeypatch
):
    """Audit P2: a cold container must not pull the whole media/evidence tree.

    ``sync_index`` (the per-request/cold path) transfers only the store index and
    never the workspace; ``list_runs`` works from that index alone; the media
    workspace transfers lazily and only for the run actually opened.
    """
    monkeypatch.setenv("AIRCHECK_S3_BUCKET", BUCKET)
    monkeypatch.setenv("AIRCHECK_S3_PREFIX", "aircheck")
    for var in ("AIRCHECK_STORE_ROOT", "AIRCHECK_WORKSPACE_ROOT", "AIRCHECK_UNIVERSE_ROOT",
                "AIRCHECK_AGENTCORE_RUNTIME_ARN"):
        monkeypatch.delenv(var, raising=False)

    s3 = SharedS3()
    warm = _service(tmp_path, "warm", universe_dir, s3)
    run_a = warm.create_run(CreateRunRequest(profile_id=BROADCAST, package_type="hero")).run_id
    run_b = warm.create_run(CreateRunRequest(profile_id=BROADCAST, package_type="hero")).run_id

    # A brand-new cold container.
    cold = _service(tmp_path, "cold", universe_dir, s3)
    s3.downloaded_keys.clear()
    cold.sync_index()
    # Index sync pulled store objects only — zero workspace media.
    assert s3.downloaded_keys, "index sync should transfer the store index"
    assert all("/store/" in k for k in s3.downloaded_keys)
    assert not any("/workspace/" in k for k in s3.downloaded_keys)

    # The Deliveries projection is available from the index alone.
    listed = cold.list_runs()
    assert {r.run_id for r in listed.runs} >= {run_a, run_b}

    # Opening ONE run hydrates only that run's workspace subtree.
    s3.downloaded_keys.clear()
    cold.get_run(run_a)
    ws = [k for k in s3.downloaded_keys if "/workspace/" in k]
    assert ws, "opening a run should hydrate its workspace"
    assert all(f"/workspace/{run_a}/" in k for k in ws)
    assert not any(f"/workspace/{run_b}/" in k for k in ws)


def b_shared(service: AIRCheckService) -> SharedS3:
    return service._workspace_mirror._s3  # type: ignore[attr-defined]


def _snapshot_of(s3: SharedS3) -> dict[tuple[str, str], str]:
    return {k: hashlib.md5(v).hexdigest() for k, v in s3.objects.items()}
