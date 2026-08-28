"""The audit trail.

An internal assistant reads customer records and company procedures on an
employee's behalf, so "who asked what, and what did the system fetch to answer
it" has to be answerable after the fact. Three things make that real rather than
decorative:

**It does not share the caller's transaction.** An authorisation denial has to
be recorded even though the request it describes is about to roll back; writing
it through the request's session would throw the record away along with it.

**It buffers, then writes once the request's own transaction has closed.** The
first version opened a second connection and wrote immediately, which is fine on
Postgres and wrong on SQLite: the caller still held the single write lock, so
every audited request sat out the busy timeout and then dropped the record.
Events are collected on the request and flushed by the database dependency the
moment it releases its connection — which fixes the contention and turns N
transactions into one. Outside a request — background ingestion, the CLI — there
is no buffer and the write happens immediately.

**It never breaks the request.** A full disk or a locked table is not a reason
to fail a technician's question, so a write failure is logged and swallowed. The
one exception is that it is not silent — an audit trail that quietly stops is
worse than one that was never there.

`audit()` reads the request id, client address and principal from the
`RequestContext`, so the retrieval pipeline and the connectors can record their
own calls without being handed an HTTP request they should not know about.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

from app.auth.rbac import Principal
from app.core.clock import utcnow
from app.core.context import current_context
from app.core.ids import new_id
from app.db.engine import get_sessionmaker
from app.db.models import AuditEvent, UsageEvent

logger = logging.getLogger("fieldops.audit")

TelemetryEvent = AuditEvent | UsageEvent

_buffer: ContextVar[list[TelemetryEvent] | None] = ContextVar("telemetry_buffer", default=None)

SUCCESS = "success"
DENIED = "denied"
ERROR = "error"


async def audit(
    action: str,
    *,
    outcome: str = SUCCESS,
    principal: Principal | None = None,
    actor_email: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    context = current_context()
    actor = principal or (context.principal if context else None)

    event = AuditEvent(
        id=new_id(),
        occurred_at=utcnow(),
        actor_user_id=actor.user_id if actor else None,
        actor_email=actor.email if actor else actor_email,
        actor_role=actor.role.value if actor else None,
        action=action,
        outcome=outcome,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=context.request_id if context else None,
        ip=context.ip if context else None,
        user_agent=context.user_agent if context else None,
        detail=json.dumps(detail, default=str) if detail else None,
    )
    await _emit(event)


async def record_usage(
    *,
    feature: str,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: int | None = None,
    cache_hit: bool = False,
    principal: Principal | None = None,
) -> None:
    """One model call, priced. Read by the cost dashboard."""
    context = current_context()
    actor = principal or (context.principal if context else None)

    event = UsageEvent(
        id=new_id(),
        occurred_at=utcnow(),
        user_id=actor.user_id if actor else None,
        feature=feature,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        request_id=context.request_id if context else None,
    )
    await _emit(event)


def open_buffer() -> list[TelemetryEvent]:
    """Start collecting this request's events. Called by the middleware."""
    events: list[TelemetryEvent] = []
    _buffer.set(events)
    return events


def close_buffer() -> None:
    _buffer.set(None)


async def flush() -> None:
    """Write whatever has accumulated on this request, in one transaction.

    Called by the database dependency at the moment it releases its connection,
    which is the earliest point at which writing cannot deadlock against the
    request's own transaction on SQLite.
    """
    await drain(_buffer.get())


async def drain(events: list[TelemetryEvent] | None) -> None:
    """Empty a specific buffer.

    Takes the list rather than reading the context variable, because the
    middleware's backstop runs as a background task — after the response, and
    therefore in a context of its own.

    Draining in place makes this safe to call twice: in the ordinary case the
    database dependency has already written the events and this finds nothing.
    """
    if not events:
        return
    pending = list(events)
    events.clear()
    await _write(pending)


async def _emit(event: TelemetryEvent) -> None:
    events = _buffer.get()
    if events is not None:
        events.append(event)
        return
    await _write([event])


async def _write(events: list[TelemetryEvent]) -> None:
    try:
        async with get_sessionmaker()() as session:
            session.add_all(events)
            await session.commit()
    except Exception:
        logger.exception("failed to write %d telemetry events", len(events))
