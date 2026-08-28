"""Time.

Everything the database stores is timezone-aware UTC. Naive datetimes are the
usual source of off-by-one-timezone bugs in audit trails, and an audit trail you
cannot trust the timestamps of is not an audit trail.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
