"""M7 durable-store tests: S3StateMirror round-trip and S3RunStore restart.

These are hermetic — an injected in-memory fake S3 client stands in for boto3, so
the suite stays offline and provider-free. They prove the S3 mirror preserves the
on-disk store byte-for-byte across a fresh process (a new production container),
which is what "deployed hero path survives restart and uses durable production
state" requires, while the frozen LocalDurableStore semantics are unchanged (see
tests/persistence/test_store.py).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from aircheck.domain.events import (
    LedgerEvent,
    LedgerEventType,
    StateTransitionPayload,
)
from aircheck.domain.state_machine import RunStateSnapshot
from aircheck.domain.status import RunStatus
from aircheck.persistence.s3_store import S3RunStore, S3StateMirror


class _FakePaginator:
    def __init__(self, store: "FakeS3") -> None:
        self._store = store

    def paginate(self, Bucket: str, Prefix: str = ""):
        contents = [
            {
                "Key": key,
                "Size": len(data),
                "ETag": '"' + hashlib.md5(data).hexdigest() + '"',
            }
            for (bucket, key), data in sorted(self._store.objects.items())
            if bucket == Bucket and key.startswith(Prefix)
        ]
        yield {"Contents": contents}


class FakeS3:
    """Minimal in-memory stand-in for the boto3 S3 client surface used here."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.downloads = 0

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.objects[(bucket, key)] = Path(filename).read_bytes()

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        dest = Path(filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.objects[(bucket, key)])
        self.downloads += 1

    def get_paginator(self, name: str) -> _FakePaginator:
        assert name == "list_objects_v2"
        return _FakePaginator(self)


def test_state_mirror_round_trips_nested_files(tmp_path: Path) -> None:
    s3 = FakeS3()
    source = tmp_path / "source"
    (source / "runs" / "run_x").mkdir(parents=True)
    (source / "top.json").write_text('{"x":1}', encoding="utf-8")
    (source / "runs" / "run_x" / "snapshot.json").write_text("nested", encoding="utf-8")

    uploaded = S3StateMirror(source, "bucket", "aircheck/state", s3_client=s3).sync_up()
    assert uploaded == 2
    # Keys are namespaced under the prefix.
    assert all(k.startswith("aircheck/state/") for (_b, k) in s3.objects)

    restored_root = tmp_path / "restored"
    downloaded = S3StateMirror(
        restored_root, "bucket", "aircheck/state", s3_client=s3
    ).sync_down()
    assert downloaded == 2
    assert (restored_root / "top.json").read_text(encoding="utf-8") == '{"x":1}'
    assert (
        restored_root / "runs" / "run_x" / "snapshot.json"
    ).read_text(encoding="utf-8") == "nested"


def test_state_mirror_incremental_skips_unchanged(tmp_path: Path) -> None:
    s3 = FakeS3()
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.json").write_text("one", encoding="utf-8")
    (source / "b.json").write_text("two", encoding="utf-8")
    mirror = S3StateMirror(source, "bucket", "state", s3_client=s3)
    assert mirror.sync_up() == 2
    # Nothing changed -> a second sync_up transfers nothing.
    assert mirror.sync_up() == 0
    # One file changes -> only that one is re-uploaded.
    (source / "a.json").write_text("one-changed", encoding="utf-8")
    assert mirror.sync_up() == 1

    # sync_down only fetches new/changed content.
    restored = tmp_path / "restored"
    down = S3StateMirror(restored, "bucket", "state", s3_client=s3)
    assert down.sync_down() == 2
    before = s3.downloads
    assert down.sync_down() == 0  # already current -> no downloads
    assert s3.downloads == before


def test_state_mirror_prefixes_are_isolated(tmp_path: Path) -> None:
    s3 = FakeS3()
    a = tmp_path / "a"
    a.mkdir()
    (a / "only_a.txt").write_text("A", encoding="utf-8")
    S3StateMirror(a, "bucket", "store", s3_client=s3).sync_up()

    # A different prefix mirror must not see the store prefix's objects.
    other_root = tmp_path / "other"
    n = S3StateMirror(other_root, "bucket", "workspace", s3_client=s3).sync_down()
    assert n == 0
    assert not (other_root / "only_a.txt").exists()


def _transition_event(run_id: str) -> LedgerEvent:
    return LedgerEvent(
        event_id="evt_001",
        run_id=run_id,
        seq=1,
        timestamp=datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
        type=LedgerEventType.STATE_TRANSITION,
        actor="SYSTEM",
        summary="ingesting",
        payload=StateTransitionPayload(
            from_state=RunStatus.CREATED,
            to_state=RunStatus.INGESTING,
        ),
        refs=(),
        prev_hash=None,
    )


def test_state_mirror_sync_down_subpath_is_scoped(tmp_path: Path) -> None:
    """M8 cold-start fix: hydrating one run's subtree transfers only that subtree."""
    s3 = FakeS3()
    source = tmp_path / "source"
    (source / "run_a").mkdir(parents=True)
    (source / "run_b").mkdir(parents=True)
    (source / "run_a" / "big.mov").write_text("a" * 100, encoding="utf-8")
    (source / "run_b" / "big.mov").write_text("b" * 100, encoding="utf-8")
    S3StateMirror(source, "bucket", "workspace", s3_client=s3).sync_up()

    restored = tmp_path / "restored"
    down = S3StateMirror(restored, "bucket", "workspace", s3_client=s3)
    # Only run_a is hydrated; run_b's media never transfers.
    assert down.sync_down(subpath="run_a") == 1
    assert (restored / "run_a" / "big.mov").exists()
    assert not (restored / "run_b").exists()
    # A second scoped sync is a no-op (content-hash skip).
    assert down.sync_down(subpath="run_a") == 0


def test_state_mirror_sync_up_subpath_is_scoped(tmp_path: Path) -> None:
    """A scoped upload persists only the named subtree, leaving siblings untouched."""
    s3 = FakeS3()
    source = tmp_path / "source"
    (source / "run_a").mkdir(parents=True)
    (source / "run_b").mkdir(parents=True)
    (source / "run_a" / "f.json").write_text("A", encoding="utf-8")
    (source / "run_b" / "f.json").write_text("B", encoding="utf-8")
    mirror = S3StateMirror(source, "bucket", "workspace", s3_client=s3)
    assert mirror.sync_up(subpath="run_a") == 1
    keys = {k for (_b, k) in s3.objects}
    assert keys == {"workspace/run_a/f.json"}  # run_b was not uploaded


def test_s3runstore_state_survives_a_fresh_process(tmp_path: Path) -> None:
    """A run committed by one S3RunStore is readable by a fresh one after sync."""
    s3 = FakeS3()
    run_id = "run_durable_001"

    # Container A: the durable store writes exactly as LocalDurableStore does.
    cache_a = tmp_path / "cache_a"
    store_a = S3RunStore(cache_a, bucket="bucket", prefix="aircheck/store", s3_client=s3)
    event = _transition_event(run_id)
    store_a.append_event(event)
    store_a.save_snapshot(
        RunStateSnapshot(run_id=run_id, status=RunStatus.INGESTING),
        require_pending_decision=False,
    )
    store_a.sync_up()

    # Container B: a brand-new process/cache rehydrates from S3 and reads the run.
    cache_b = tmp_path / "cache_b"
    assert not (cache_b / "runs" / run_id / "snapshot.json").exists()
    store_b = S3RunStore(cache_b, bucket="bucket", prefix="aircheck/store", s3_client=s3)
    store_b.sync_down()

    restored = store_b.load_snapshot(run_id)
    assert restored.status is RunStatus.INGESTING
    assert restored.run_id == run_id
    assert store_b.list_events(run_id) == (event,)


def test_s3runstore_is_a_local_durable_store(tmp_path: Path) -> None:
    """The adapter inherits the full frozen store surface (no reimplementation)."""
    from aircheck.persistence.store import LocalDurableStore

    store = S3RunStore(tmp_path / "c", bucket="b", prefix="p", s3_client=FakeS3())
    assert isinstance(store, LocalDurableStore)
    for method in ("save_snapshot", "load_snapshot", "append_event", "consume", "void"):
        assert hasattr(store, method)
