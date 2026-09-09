"""Per-run S3 mutation lease for cross-container decision safety (M8).

The M7 ``S3RunStore`` preserves ``LocalDurableStore``'s in-process single-use
authorization semantics, but the account concurrency limit prevents pinning the
API Lambda to a single container, so two warm containers can observe the same
``PENDING`` decision and both mutate it (last-writer-wins over the S3 mirror).
The M7 independent audit reproduced exactly this: concurrent APPROVE and DENY on
one pending decision both returned success.

``S3RunLease`` closes that gap with an S3-native compare-and-swap. A mutation of a
given run must hold an exclusive per-run lease created with an S3 conditional
write (``If-None-Match: *``). Exactly one concurrent writer creates the lease; the
loser raises :class:`ConflictError` (surfaced as a product-safe HTTP 409). A stale
lease (a container that died mid-mutation) is taken over only after its TTL, via a
conditional overwrite (``If-Match`` on the observed ETag) so two racers cannot both
take it over.

The lease is coordination only. It is never authoritative run state, never
mirrored into the durable store, and carries no authority. Durable truth stays in
``S3RunStore``; the caller re-reads authoritative state *under* the lease and fails
closed if the state it meant to mutate is already gone (consumed by the winner).
S3 conditional writes are used only for this ephemeral lock, so the frozen store
semantics are untouched.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

_PRECONDITION_CODES = {"PreconditionFailed", "412"}
_NOT_FOUND_CODES = {"NoSuchKey", "NotFound", "404"}


class ConflictError(RuntimeError):
    """A per-run mutation could not proceed: another writer holds or won the lease."""


class S3RunLease:
    """Exclusive per-run advisory lease backed by S3 conditional writes.

    ``bucket``/``prefix`` place the lock objects at ``<prefix>/<run_id>.lock``.
    Keep ``prefix`` OUTSIDE the mirrored store/workspace prefixes so the lease is
    never confused with durable state. ``ttl_s`` bounds how long a crashed holder
    blocks others before its lease may be taken over.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "locks",
        *,
        s3_client: Any = None,
        ttl_s: float = 120.0,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._s3 = s3_client
        self._ttl_s = float(ttl_s)

    @property
    def s3(self) -> Any:
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3")
        return self._s3

    def _key(self, run_id: str) -> str:
        return f"{self._prefix}/{run_id}.lock" if self._prefix else f"{run_id}.lock"

    @staticmethod
    def _code(exc: Any) -> str:
        try:
            return exc.response.get("Error", {}).get("Code", "")
        except AttributeError:
            return ""

    def _body(self, token: str) -> bytes:
        now = time.time()
        return json.dumps(
            {"owner": token, "acquired_at": now, "expires_at": now + self._ttl_s}
        ).encode("utf-8")

    def _try_create(self, key: str, body: bytes) -> str | None:
        """PUT if absent (``If-None-Match: *``). Return the ETag, or None on conflict."""
        from botocore.exceptions import ClientError

        try:
            resp = self.s3.put_object(
                Bucket=self._bucket, Key=key, Body=body, IfNoneMatch="*"
            )
            return resp.get("ETag")
        except ClientError as exc:
            if self._code(exc) in _PRECONDITION_CODES:
                return None
            raise

    def _read(self, key: str) -> tuple[str | None, dict[str, Any]]:
        from botocore.exceptions import ClientError

        try:
            obj = self.s3.get_object(Bucket=self._bucket, Key=key)
            etag = obj.get("ETag")
            raw = obj["Body"].read()
            try:
                meta = json.loads(raw)
            except Exception:
                meta = {}
            return etag, meta if isinstance(meta, dict) else {}
        except ClientError as exc:
            if self._code(exc) in _NOT_FOUND_CODES:
                return None, {}
            raise

    def _try_takeover(self, key: str, body: bytes, etag: str) -> str | None:
        """Overwrite only if the ETag still matches (``If-Match``). None on conflict."""
        from botocore.exceptions import ClientError

        try:
            resp = self.s3.put_object(
                Bucket=self._bucket, Key=key, Body=body, IfMatch=etag
            )
            return resp.get("ETag")
        except ClientError as exc:
            if self._code(exc) in _PRECONDITION_CODES:
                return None
            raise

    def _acquire(self, run_id: str) -> tuple[str, str]:
        key = self._key(run_id)
        token = uuid.uuid4().hex
        body = self._body(token)

        etag = self._try_create(key, body)
        if etag is not None:
            return token, etag

        # The lock exists. Take it over only if it has expired.
        cur_etag, meta = self._read(key)
        if cur_etag is None:
            # Deleted between our create attempt and the read: race the create once.
            etag = self._try_create(key, body)
            if etag is not None:
                return token, etag
            raise ConflictError(run_id)
        if time.time() <= float(meta.get("expires_at", 0) or 0):
            raise ConflictError(run_id)
        etag = self._try_takeover(key, body, cur_etag)
        if etag is None:
            raise ConflictError(run_id)
        return token, etag

    def _release(self, run_id: str, etag: str) -> None:
        """Delete the lock only if we still own it (``If-Match``); never clobber a newer holder."""
        from botocore.exceptions import ClientError

        try:
            self.s3.delete_object(Bucket=self._bucket, Key=self._key(run_id), IfMatch=etag)
        except ClientError:
            # Our lease expired and was taken over/removed; leave the newer holder alone.
            pass

    @contextmanager
    def hold(self, run_id: str) -> Iterator[None]:
        """Hold the per-run lease for the duration of the ``with`` block.

        Raises :class:`ConflictError` immediately if the lease cannot be acquired.
        """
        token, etag = self._acquire(run_id)
        try:
            yield
        finally:
            self._release(run_id, etag)


__all__ = ["S3RunLease", "ConflictError"]
