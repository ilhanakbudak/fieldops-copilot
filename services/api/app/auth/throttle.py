"""Login throttling.

Argon2 makes offline cracking expensive; it does nothing about someone trying a
hundred common passwords against a known company email address online. Failures
are counted per email *and* per client address, so neither a single targeted
account nor a single host spraying many accounts gets unlimited attempts.

In-process and therefore per-instance: correct for one container, and
approximate behind a load balancer. Postgres or Redis is the right home for this
once there is more than one instance, and the interface below does not change
when it moves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.clock import utcnow


@dataclass
class _Bucket:
    failures: int = 0
    first_failure_at: datetime = field(default_factory=utcnow)
    locked_until: datetime | None = None


class LoginThrottle:
    def __init__(self, max_attempts: int, lockout: timedelta) -> None:
        self._max_attempts = max_attempts
        self._lockout = lockout
        self._buckets: dict[str, _Bucket] = {}

    def _bucket(self, key: str) -> _Bucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket()
            self._buckets[key] = bucket
        return bucket

    def retry_after(self, keys: list[str]) -> int | None:
        """Seconds the caller must wait, or None if they may try now."""
        now = utcnow()
        waits = [
            int((bucket.locked_until - now).total_seconds())
            for key in keys
            if (bucket := self._buckets.get(key)) is not None
            and bucket.locked_until is not None
            and bucket.locked_until > now
        ]
        return max(waits) if waits else None

    def record_failure(self, keys: list[str]) -> None:
        now = utcnow()
        for key in keys:
            bucket = self._bucket(key)
            # The window slides: attempts spread thinly over hours should not
            # eventually add up to a lockout for a forgetful employee.
            if now - bucket.first_failure_at > self._lockout:
                bucket.failures = 0
                bucket.first_failure_at = now
            bucket.failures += 1
            if bucket.failures >= self._max_attempts:
                bucket.locked_until = now + self._lockout

    def record_success(self, keys: list[str]) -> None:
        for key in keys:
            self._buckets.pop(key, None)

    def reset(self) -> None:
        self._buckets.clear()
