"""Fan-out to the people who are allowed to see it.

A telephone rings and the office needs to know inside a second. Nobody asked for
this, so there is no request to answer it — it has to be pushed, which means a
socket held open per person and something to write to all of them at once.

The interesting decisions are all about who "all of them" is.

**Subscribers are principals, not sockets.** A connection joins by handing over
the employee it belongs to, and the hub refuses anybody without
`calls:assist`. The gate is here rather than only on the route because this is
the object that does the sending, and a broadcast helper that will send to
whatever is in its list is one careless `subscribe()` away from putting a
customer's service history on a technician's screen.

**A slow or dead socket cannot hold up the others.** Each subscriber has a
bounded queue and a write that would block is dropped for that subscriber alone.
A screen pop is worth having in the first second and worthless in the thirtieth,
so dropping is the correct failure — the alternative is one browser on a bad
connection delaying the pop for everybody in the office.

**It is in-process, and that is a real limit rather than an oversight.** Two API
instances behind a load balancer would each hold half the office's sockets, and a
webhook arriving at one would pop only that half's screens. The fix is a Redis
or Postgres `LISTEN/NOTIFY` fan-out behind this same interface — `publish` and
`subscribe` do not change shape — and it is not written here because a
single-instance deployment is the honest scope of this repository. Said out loud
in docs/DEPLOYMENT.md rather than discovered at the second instance.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.auth.rbac import Permission, Principal
from app.core.errors import AuthorizationError

logger = logging.getLogger("fieldops.realtime")

# One screen pop is a few kilobytes and a person can read about one a second.
# Ten is a generous backlog; a subscriber that far behind is not reading.
QUEUE_DEPTH = 10


class CallHub:
    """Everyone currently watching for incoming calls."""

    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue[dict[str, Any]], Principal] = {}

    @property
    def watching(self) -> int:
        return len(self._subscribers)

    @asynccontextmanager
    async def subscribe(self, principal: Principal) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        """Join the fan-out for as long as the block runs.

        A context manager rather than a subscribe/unsubscribe pair, so a socket
        that dies mid-read cannot leave its queue in the dictionary — which
        would be a slow leak of a few kilobytes per dropped connection and,
        worse, a principal the hub still thinks is watching.
        """
        if not principal.can(Permission.CALLS_ASSIST):
            raise AuthorizationError("Your role does not have access to this.")

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_DEPTH)
        self._subscribers[queue] = principal
        logger.info("call watch opened for %s (%d watching)", principal.email, self.watching)
        try:
            yield queue
        finally:
            self._subscribers.pop(queue, None)
            logger.info("call watch closed for %s (%d watching)", principal.email, self.watching)

    def publish(self, message: dict[str, Any]) -> int:
        """Send to every watcher. Returns how many received it.

        Synchronous on purpose: `put_nowait` is the whole point. An `await
        queue.put()` would make a full queue block the webhook that is trying
        to notify everybody else.
        """
        delivered = 0
        for queue, principal in self._subscribers.items():
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("dropped a call notification for %s: queue full", principal.email)
                continue
            delivered += 1
        return delivered


_hub: CallHub | None = None


def get_call_hub() -> CallHub:
    global _hub
    if _hub is None:
        _hub = CallHub()
    return _hub


def reset_call_hub() -> None:
    """Between tests. A hub is process state and the suite runs many apps in one."""
    global _hub
    _hub = None
