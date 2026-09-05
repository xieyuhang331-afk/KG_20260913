from __future__ import annotations

import hashlib
import hmac
import math
import threading
import time
from dataclasses import dataclass
from ipaddress import ip_address

from fastapi import HTTPException


@dataclass
class _Bucket:
    count: int
    pending: int
    expires: float


def _reject(status: int, code: str, seconds: float = 1) -> HTTPException:
    return HTTPException(
        status_code=status, detail=code,
        headers={"Cache-Control": "no-store", "Retry-After": str(max(1, math.ceil(seconds)))},
    )


class AuthRateLimiter:
    """Single-process bounded quotas; active entries are never evicted for capacity."""

    def __init__(self, key: bytes, *, clock=time.monotonic, capacity: int = 4096):
        if not isinstance(key, bytes) or len(key) < 32 or type(capacity) is not int or capacity < 3:
            raise ValueError("AUTH_RATE_LIMIT_CONFIGURATION_INVALID")
        self._key = key
        self._clock = clock
        self._capacity = capacity
        self._lock = threading.Lock()
        self._buckets = {}
        self._reservations = {}
        self._sequence = 0

    def _digest(self, purpose: str, *values: str) -> bytes:
        encoded = purpose.encode("ascii") + b";"
        for value in values:
            part = value.encode("utf-8")
            encoded += str(len(part)).encode("ascii") + b":" + part
        return hmac.new(self._key, encoded, hashlib.sha256).digest()

    @staticmethod
    def _peer(peer: str) -> str:
        try:
            return str(ip_address(peer))
        except (ValueError, TypeError):
            raise _reject(503, "AUTH_PEER_UNAVAILABLE") from None

    def _reap(self, now: float):
        for key, bucket in list(self._buckets.items()):
            if bucket.expires <= now:
                if bucket.pending == 0:
                    del self._buckets[key]
                else:
                    bucket.count = 0

    def _check(self, rules, now: float, *, reservation: bool):
        self._reap(now)
        for key, limit, _ in rules:
            bucket = self._buckets.get(key)
            if bucket is not None and bucket.count + bucket.pending >= limit:
                raise _reject(429, "AUTH_RATE_LIMITED", bucket.expires - now)
        additional = sum(key not in self._buckets for key, _, _ in rules)
        if (len(self._buckets) + additional > self._capacity
                or reservation and len(self._reservations) >= self._capacity):
            raise _reject(503, "AUTH_RATE_LIMIT_CAPACITY")
        for key, _, window in rules:
            self._buckets.setdefault(key, _Bucket(0, 0, now + window))

    def reserve_login(self, peer: str, subject: str) -> int:
        peer = self._peer(peer)
        ip_key = self._digest("login-ip", peer)
        subject_key = self._digest("login-subject", subject)
        pair_key = self._digest("login-subject-ip", subject, peer)
        rules = ((ip_key, 20, 300), (subject_key, 5, 900), (pair_key, 5, 900))
        with self._lock:
            now = self._clock()
            self._check(rules, now, reservation=True)
            self._buckets[ip_key].count += 1
            for key in (subject_key, pair_key):
                self._buckets[key].pending += 1
            self._sequence += 1
            self._reservations[self._sequence] = (subject_key, pair_key)
            return self._sequence

    def settle_login(self, reservation: int, *, failed: bool) -> None:
        with self._lock:
            keys = self._reservations.pop(reservation, None)
            if keys is None:
                return
            now = self._clock()
            for key in keys:
                bucket = self._buckets[key]
                bucket.pending -= 1
                if bucket.expires <= now:
                    bucket.count = 0
                if failed:
                    if bucket.count == 0:
                        bucket.expires = now + 900
                    bucket.count += 1
                    if bucket.count >= 5:
                        bucket.expires = now + 900

    def reserve_registration(self, peer: str, subject: str) -> None:
        peer = self._peer(peer)
        rules = (
            (self._digest("register-ip", peer), 5, 600),
            (self._digest("register-subject", subject), 3, 86400),
        )
        with self._lock:
            now = self._clock()
            self._check(rules, now, reservation=False)
            for key, _, _ in rules:
                self._buckets[key].count += 1
