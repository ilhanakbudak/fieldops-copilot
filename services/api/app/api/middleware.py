"""Request context and the access log.

Every response carries an `X-Request-Id`. The same id lands on every audit and
usage record written while serving that request, so one identifier ties a
support report to the exact retrieval calls, connector calls and model spend
behind it.
"""

from __future__ import annotations

import logging
import time

from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.audit.log import close_buffer, drain, open_buffer
from app.core.context import RequestContext, use_context
from app.core.ids import new_id

logger = logging.getLogger("fieldops.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Honour an inbound id so a trace survives the Next.js proxy hop, but
        # only in a shape we control — it ends up in log lines and audit rows.
        inbound = request.headers.get("x-request-id", "")
        request_id = inbound if _is_safe_id(inbound) else new_id()

        context = RequestContext(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )

        started = time.perf_counter()
        with use_context(context):
            events = open_buffer()
            try:
                response = await call_next(request)
            finally:
                close_buffer()

        duration_ms = int((time.perf_counter() - started) * 1000)
        # Backstop, and only that: the database dependency has normally written
        # these already. It has to run as a background task because `call_next`
        # returns as soon as the response *starts* — the endpoint's session is
        # still open at that point, and writing there is the deadlock this whole
        # arrangement exists to avoid.
        response.background = BackgroundTask(drain, events)
        response.headers["X-Request-Id"] = request_id
        logger.info(
            "%s %s %s %dms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={"request_id": request_id},
        )
        return response


def _is_safe_id(value: str) -> bool:
    return 0 < len(value) <= 64 and all(c.isalnum() or c in "-_" for c in value)


def client_ip(request: Request) -> str | None:
    """The address the request came from.

    `X-Forwarded-For` is trusted only because the deployment sits behind a proxy
    that sets it; the leftmost entry is the client. Behind no proxy this header
    is caller-controlled, which is why the value is used for throttling and
    audit context and never for authorisation.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host if request.client else None
