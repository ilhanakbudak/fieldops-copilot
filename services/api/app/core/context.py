"""Request-scoped context.

Audit records need the request id, caller and client details. Threading a
`Request` object down through the retrieval pipeline and every connector would
put HTTP concerns in places that have no business knowing about HTTP, so the
context is carried in a `ContextVar` instead and read by whoever needs it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from app.auth.rbac import Principal


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    method: str
    path: str
    ip: str | None = None
    user_agent: str | None = None
    principal: Principal | None = None


_current: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def current_context() -> RequestContext | None:
    return _current.get()


@contextmanager
def use_context(context: RequestContext) -> Iterator[RequestContext]:
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def attach_principal(principal: Principal) -> None:
    """Record who the caller turned out to be, once authentication has run.

    The context is created by middleware before the route's dependencies
    resolve, so the principal is only known a moment later.
    """
    context = _current.get()
    if context is not None:
        _current.set(
            RequestContext(
                request_id=context.request_id,
                method=context.method,
                path=context.path,
                ip=context.ip,
                user_agent=context.user_agent,
                principal=principal,
            )
        )
