"""S3-backed durable production RunStore for the M7 deployment.

``S3RunStore`` preserves EVERY frozen ``LocalDurableStore`` semantic — atomic
writes, exclusive-file locks, WAL-backed action/transition recovery, and
single-use authorization consume/void — by using a local cache directory as the
working store exactly as ``LocalDurableStore`` does, and mirroring that directory
to and from an S3 prefix as the durable system of record. On a fresh process
(for example a new AWS Lambda execution container) ``sync_down()`` rehydrates the
cache from S3 before any work; after each mutation the application boundary calls
``sync_up()`` to persist the change durably. Production container disk is
disposable workspace/cache only (``ARCHITECTURE.md`` persistence section, D-007);
the durable authority is S3.

The deterministic runtime is unchanged. The ``HeadlessOrchestrator`` still writes
through the same local ``store_root`` path with its own ``LocalDurableStore``, so
no runtime, authority, recovery, or terminal-computation logic moves into S3 —
this adapter only mirrors the on-disk cache. ``S3StateMirror`` provides the same
durable mirror for the per-run media/evidence workspace, whose evidence artifacts
must survive a container restart for the Evidence surface to remain truthful.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from aircheck.persistence.store import LocalDurableStore


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class S3StateMirror:
    """Mirror one local directory tree to and from an S3 prefix.

    The local tree is the working copy; the S3 prefix is the durable record.
    ``sync_down`` restores the tree on a fresh process and ``sync_up`` persists
    it after mutations. boto3 is imported lazily so importing this module never
    requires AWS libraries in local/dev/test environments.
    """

    def __init__(
        self,
        local_root: str | Path,
        bucket: str,
        prefix: str,
        *,
        s3_client: Any = None,
    ) -> None:
        self.local_root = Path(local_root).resolve()
        self.local_root.mkdir(parents=True, exist_ok=True)
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._s3 = s3_client

    @property
    def s3(self) -> Any:
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3")
        return self._s3

    def _base(self) -> str:
        return f"{self.prefix}/" if self.prefix else ""

    def _key(self, rel: str) -> str:
        rel = rel.replace("\\", "/").lstrip("/")
        return f"{self.prefix}/{rel}" if self.prefix else rel

    @staticmethod
    def _norm_subpath(subpath: str | None) -> str:
        return (subpath or "").replace("\\", "/").strip("/")

    def _remote_index(self, subpath: str | None = None) -> dict[str, str]:
        """Map object keys under the prefix (optionally one subtree) to their ETag.

        Passing ``subpath`` narrows the S3 listing to ``<prefix>/<subpath>`` so a
        single run's tree can be hydrated without paging the whole state tree —
        the M8 cold-start fix (only the accessed run's media/evidence transfers).
        """
        index: dict[str, str] = {}
        sub = self._norm_subpath(subpath)
        list_prefix = self._base() + (f"{sub}/" if sub else "")
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=list_prefix):
            for obj in page.get("Contents", []):
                index[obj["Key"]] = obj.get("ETag", "").strip('"')
        return index

    def sync_down(self, subpath: str | None = None) -> int:
        """Download objects that are new or content-changed (MD5/ETag skip).

        Incremental so it is cheap to call per request: an object already present
        locally with a matching content hash is not re-downloaded. ``subpath``
        restricts the transfer to one relative subtree (e.g. a single run id).
        """
        count = 0
        base = self._base()
        for key, etag in self._remote_index(subpath).items():
            rel = key[len(base):] if base else key
            if not rel or rel.endswith("/"):
                continue
            dest = self.local_root / rel
            if dest.exists() and "-" not in etag and _md5(dest) == etag:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.s3.download_file(self.bucket, key, str(dest))
            count += 1
        return count

    def sync_up(self, subpath: str | None = None) -> int:
        """Upload local files that are new or content-changed (MD5/ETag skip).

        ``subpath`` restricts the scan/transfer to one relative subtree so a single
        mutated run persists without rescanning the whole workspace tree.
        """
        count = 0
        sub = self._norm_subpath(subpath)
        scan_root = self.local_root / sub if sub else self.local_root
        if not scan_root.exists():
            return 0
        remote = self._remote_index(subpath)
        for path in scan_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.local_root).as_posix()
            key = self._key(rel)
            etag = remote.get(key)
            if etag is not None and "-" not in etag and _md5(path) == etag:
                continue
            self.s3.upload_file(str(path), self.bucket, key)
            count += 1
        return count


class S3RunStore(LocalDurableStore):
    """``LocalDurableStore`` whose durable system of record is an S3 prefix.

    Every store operation runs over a local cache root with identical semantics
    to ``LocalDurableStore`` (this class only adds S3 mirroring), so the frozen
    reliability guarantees — atomic snapshot/event writes, monotonic sequence and
    hash-chained ledger, WAL-backed action/transition recovery, and opaque
    single-use authorization consume/void — are unchanged. ``sync_down`` and
    ``sync_up`` move that cache to and from S3, and ``sync_down`` re-runs WAL
    recovery in case a rehydrated cache carried a pending transaction.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        bucket: str,
        prefix: str = "store",
        s3_client: Any = None,
    ) -> None:
        super().__init__(root)
        self._mirror = S3StateMirror(self._root, bucket, prefix, s3_client=s3_client)

    @property
    def bucket(self) -> str:
        return self._mirror.bucket

    @property
    def prefix(self) -> str:
        return self._mirror.prefix

    def sync_down(self) -> int:
        n = self._mirror.sync_down()
        # A rehydrated cache may carry a pending action/transition journal that a
        # prior container crashed mid-commit; finish it deterministically.
        self._recover_action_transactions()
        self._recover_transition_transactions()
        return n

    def sync_up(self) -> int:
        return self._mirror.sync_up()


__all__ = ["S3RunStore", "S3StateMirror"]
