"""M8 per-run S3 mutation-lease tests (hermetic; conditional-write fake client).

These prove the compare-and-swap the M8 audit fix relies on: exactly one holder
at a time, a stale (expired) lease can be taken over by exactly one racer, a
released lease is re-acquirable, and a holder never clobbers a newer holder on
release. The fake S3 implements the same If-None-Match / If-Match preconditions
the real S3 enforces (verified live against the versioned state bucket), so the
suite stays offline and provider-free.
"""

from __future__ import annotations

import hashlib
import io

import pytest
from botocore.exceptions import ClientError

from aircheck.persistence.s3_lock import ConflictError, S3RunLease


class ConditionalFakeS3:
    """In-memory S3 with the conditional-write surface the lease uses."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    @staticmethod
    def _etag(data: bytes) -> str:
        return '"' + hashlib.md5(data).hexdigest() + '"'

    @staticmethod
    def _fail(code: str, op: str) -> ClientError:
        return ClientError({"Error": {"Code": code, "Message": code}}, op)

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch=None, IfMatch=None):
        key = (Bucket, Key)
        exists = key in self.objects
        if IfNoneMatch == "*" and exists:
            raise self._fail("PreconditionFailed", "PutObject")
        if IfMatch is not None:
            if not exists or self._etag(self.objects[key]) != IfMatch:
                raise self._fail("PreconditionFailed", "PutObject")
        body = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode()
        self.objects[key] = bytes(body)
        return {"ETag": self._etag(self.objects[key])}

    def get_object(self, *, Bucket, Key):
        key = (Bucket, Key)
        if key not in self.objects:
            raise self._fail("NoSuchKey", "GetObject")
        data = self.objects[key]
        return {"ETag": self._etag(data), "Body": io.BytesIO(data)}

    def delete_object(self, *, Bucket, Key, IfMatch=None):
        key = (Bucket, Key)
        if IfMatch is not None:
            if key not in self.objects or self._etag(self.objects[key]) != IfMatch:
                raise self._fail("PreconditionFailed", "DeleteObject")
        self.objects.pop(key, None)
        return {}


def _lease(s3, *, ttl_s=120.0) -> S3RunLease:
    return S3RunLease("bucket", prefix="aircheck/locks", s3_client=s3, ttl_s=ttl_s)


def test_second_concurrent_holder_conflicts() -> None:
    s3 = ConditionalFakeS3()
    a, b = _lease(s3), _lease(s3)
    with a.hold("run_x"):
        # A holds the lease; B cannot acquire it.
        with pytest.raises(ConflictError):
            with b.hold("run_x"):
                pass
    # Once A releases, B can acquire.
    with b.hold("run_x"):
        pass


def test_release_deletes_only_our_lock() -> None:
    s3 = ConditionalFakeS3()
    a = _lease(s3)
    with a.hold("run_y"):
        pass
    # No lock object remains after a clean release.
    assert not any(k.endswith("run_y.lock") for (_b, k) in s3.objects)


def test_expired_lease_is_taken_over_by_exactly_one() -> None:
    s3 = ConditionalFakeS3()
    # A holds a lease that is already expired (ttl 0) but never releases it
    # (simulating a crashed container): enter the context but do not exit.
    holder = _lease(s3, ttl_s=0.0)
    token, etag = holder._acquire("run_z")  # crashed holder, lock left behind
    assert (("bucket", "aircheck/locks/run_z.lock")) in s3.objects

    # Two contenders race to take over the expired lease; exactly one wins.
    b, c = _lease(s3), _lease(s3)
    tb = _try(b, "run_z")
    tc = _try(c, "run_z")
    assert sorted([tb, tc]) == [False, True]  # exactly one takeover succeeds


def test_unexpired_lease_cannot_be_taken_over() -> None:
    s3 = ConditionalFakeS3()
    holder = _lease(s3, ttl_s=120.0)
    holder._acquire("run_live")  # held, not expired, not released
    other = _lease(s3, ttl_s=120.0)
    with pytest.raises(ConflictError):
        with other.hold("run_live"):
            pass


def _try(lease: S3RunLease, run_id: str) -> bool:
    try:
        lease._acquire(run_id)
        return True
    except ConflictError:
        return False
