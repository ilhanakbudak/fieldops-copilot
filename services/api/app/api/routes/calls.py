"""Caller lookup: a phone rings, and the record is already on the screen.

Two endpoints that could hardly be less alike.

`POST /calls/incoming` is the only route in this application that an
unauthenticated stranger is supposed to be able to reach. It is a webhook: the
phone system POSTs to it, at a moment nobody chose, with a body that is
attacker-controlled until the connector proves otherwise. Everything below about
it follows from that one fact.

`GET /calls/stream` is a WebSocket the office holds open, waiting for the phone
to ring. It is authenticated by the same session cookie as every other route and
gated on `calls:assist`, which office staff and administrators hold and
technicians and salespeople do not.

`GET /calls/assist` is the other half: a WebSocket carrying a live transcript in
and suggestions out, for the duration of one call. Same gate. See
`app/realtime/assist.py` for why most of what it is sent is deliberately
ignored.

## The webhook answers the same way whatever happens

204, always — for a verified call, for a bad token, for a payload that is not a
call at all. There is nothing to say to the caller, and everything one could say
is worth knowing to somebody who should not know it. A 401 for a bad token and a
204 for a good one is an oracle for guessing the token. A 404 for an unrecognised
number and a 204 for a recognised one is a way to ask this business, at whatever
rate you like, which of ten thousand phone numbers are its customers — without
ever signing in. The screen pop goes to the office over a different connection
entirely, and the webhook's reply carries none of it.

Note what this costs: a misconfigured phone system gets 204 too, and looks fine
while delivering nothing. That is what the log line and the audit record are
for. The endpoint is silent to its caller, not to its operator.

## Nothing from the payload is trusted with anything but a lookup

The number is normalised and used to search the CRM. It is not written to the
database, not passed to a model, and not rendered as anything but text. The
worst a forged delivery can do is put a plausible-looking call on the office's
screen — which is a nuisance, and is why the token is checked in constant time
before any of it runs.

## Guessing the token

Verification is a secret comparison, so an attacker who wants to guess it can
ask as often as they like — and a 204 either way means they cannot tell when
they are right, but they can still try. The same per-IP throttle the login route
uses is applied here, keyed on the address rather than an email, so a host that
sends a run of unverifiable deliveries stops being answered. A real phone system
never fails verification, so the only caller this affects is one that should not
be there.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect, status

from app.api.deps import DbDep, SettingsDep, require
from app.api.middleware import client_ip
from app.api.routes.customers import detail_out, summary_out
from app.api.schemas import (
    CallDemoNumber,
    CallDemoOut,
    CallScript,
    CustomerDetailOut,
    InboundCallOut,
    ScreenPopOut,
    TranscriptFragment,
)
from app.audit import DENIED, ERROR, SUCCESS, audit, record_usage
from app.auth.rbac import Permission, Principal
from app.auth.service import get_throttle
from app.auth.sessions import resolve_session
from app.config import Settings
from app.connectors import (
    CrmUnavailableError,
    InboundCall,
    MockTelephonyConnector,
    get_crm,
    get_telephony,
)
from app.core.context import attach_principal
from app.core.errors import NotFoundError
from app.core.ids import new_id
from app.db.engine import session_scope
from app.llm import get_llm_provider
from app.llm.pricing import cost_usd
from app.realtime import get_call_hub
from app.realtime.assist import Suggestion, classify, suggest
from app.realtime.transcript import Speaker, TranscriptBuffer

logger = logging.getLogger("fieldops.calls")

router = APIRouter(prefix="/calls", tags=["calls"])

# How many jobs a screen pop carries. Enough for "we were there in April about
# the brine valve", short enough to read standing up.
RECENT_JOBS = 3

# A heartbeat, so a proxy that closes idle connections does not quietly take the
# office's screen pops away. Shorter than the 60 seconds most of them use.
PING_SECONDS = 25.0


@router.post("/incoming", status_code=status.HTTP_204_NO_CONTENT)
async def incoming_call(request: Request, settings: SettingsDep) -> Response:
    """A phone rang. See the module docstring for why this always returns 204."""
    throttle = get_throttle(settings)
    keys = [f"call-webhook:{client_ip(request) or 'unknown'}"]

    if throttle.retry_after(keys) is not None:
        # Not a 429. The response is 204 whatever happens here, for the reasons
        # in the module docstring, and a status that changed under load would
        # be the same oracle by a slower route.
        logger.warning("throttled a call webhook from %s", client_ip(request))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    body = await request.body()
    connector = get_telephony()
    headers = dict(request.headers)

    if not connector.verify(headers, body):
        throttle.record_failure(keys)
        await audit(
            "call.webhook",
            outcome=DENIED,
            detail={"reason": "verification failed", "provider": connector.name},
        )
        logger.warning("rejected a call webhook: verification failed")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    throttle.record_success(keys)

    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    call = connector.parse(payload)
    if call is None:
        # The ordinary case: phone systems deliver every state change on a
        # call, and one of them is the one worth a screen pop.
        logger.debug("call webhook carried no ringing event")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    await _pop(call, connector.name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _pop(call: InboundCall, provider: str) -> None:
    """Look the number up and put the result on the office's screens."""
    matches = []
    detail = None
    outcome = SUCCESS

    try:
        matches = await get_crm().find_by_phone(call.from_number)
        # One match, and only one, is opened. Two accounts on one number means
        # the person answering chooses — see `ScreenPopOut`.
        if len(matches) == 1:
            detail = await get_crm().get_customer(matches[0].id)
    except CrmUnavailableError as error:
        # The pop still goes out. "The phone is ringing and the CRM is down" is
        # something the office needs to know *more* than usual, not less.
        logger.warning("CRM unavailable during a caller lookup: %s", error)
        outcome = ERROR

    pop = ScreenPopOut(
        call=InboundCallOut(
            call_id=call.call_id,
            from_number=call.from_number,
            to_number=call.to_number,
            received_at=call.received_at,
        ),
        matches=[summary_out(customer) for customer in matches],
        detail=_trimmed(detail),
    )

    delivered = get_call_hub().publish(pop.model_dump(by_alias=True))

    await audit(
        "call.incoming",
        outcome=outcome,
        resource_type="customer" if len(matches) == 1 else "phone",
        resource_id=matches[0].id if len(matches) == 1 else call.digits,
        detail={
            "provider": provider,
            "callId": call.call_id,
            # The normalised number, not the raw one: the audit trail is read
            # by people looking for a specific call, and they will have it in
            # whatever format their phone showed them.
            "from": call.digits,
            "matched": [customer.id for customer in matches],
            # Zero here and a customer complaining they were not called back is
            # the same incident. Worth being able to see it.
            "delivered": delivered,
        },
    )
    logger.info(
        "call from %s: %d match(es), delivered to %d screen(s)",
        call.digits or "unknown",
        len(matches),
        delivered,
    )


def _trimmed(detail: Any) -> CustomerDetailOut | None:
    if detail is None:
        return None
    out = detail_out(detail)
    return out.model_copy(update={"jobs": out.jobs[:RECENT_JOBS]})


# --- Making the phone ring, without a phone ---------------------------------


@router.get("/demo", response_model=CallDemoOut, dependencies=[require(Permission.CALLS_ASSIST)])
async def demo_calls(settings: SettingsDep) -> CallDemoOut:
    """What the demo page needs to make a call arrive.

    It hands back the mock connector's verification token and a few numbers
    worth ringing from, and then the *browser* posts the webhook — through
    verification, parsing, lookup and fan-out, exactly as a phone system would.
    A demo button wired straight into `_pop` would skip the two steps most
    worth showing, and would leave the verification branch untested by the only
    path a reviewer actually runs.

    Signed in, gated on `calls:assist`, and refused outside demo mode. The
    token is a demo secret and it is still a secret: an unauthenticated route
    handing it out would make the webhook forgeable by anybody.
    """
    connector = get_telephony()
    if not settings.demo_mode or not isinstance(connector, MockTelephonyConnector):
        raise NotFoundError("No such endpoint.")

    crm = get_crm()
    numbers = [
        CallDemoNumber(number=number, label=label) for number, label in await _demo_numbers(crm)
    ]
    return CallDemoOut(
        token=connector.token,
        header="X-FieldOps-Call-Token",
        numbers=numbers,
        script=_call_script(),
    )


# app/api/routes/ → app → services/api → services → repository root
CALL_SCRIPT = Path(__file__).resolve().parents[5] / "fixtures" / "calls" / "warm-water.json"


def _call_script() -> CallScript | None:
    """The scripted call, for demonstrating live assistance without a phone.

    Read per request rather than cached: it is one small file, only in demo
    mode, and editing it and reloading the page is how anybody would want to
    try a different conversation.
    """
    try:
        payload = json.loads(CALL_SCRIPT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("no call script at %s", CALL_SCRIPT)
        return None

    return CallScript(
        customer_id=payload.get("customerId"),
        fragments=[
            TranscriptFragment(
                delay_ms=int(item.get("delayMs", 500)),
                speaker="agent" if item.get("speaker") == "agent" else "caller",
                text=str(item.get("text", "")),
                final=bool(item.get("final")),
            )
            for item in payload.get("fragments", [])
        ],
    )


async def _demo_numbers(crm: Any) -> list[tuple[str, str]]:
    """One number per outcome the screen pop has to handle."""
    known = await crm.search_customers("Raman", limit=1)
    shared = await crm.search_customers("Okonkwo", limit=5)

    numbers: list[tuple[str, str]] = []
    if known:
        numbers.append((known[0].phone, f"{known[0].name} — one match"))
    if shared:
        numbers.append((shared[0].phone, f"{len(shared)} accounts on one number"))
    numbers.append(("(207) 555-0000", "Not in the CRM"))
    return numbers


# --- The office's end -------------------------------------------------------


@router.websocket("/stream")
async def stream_calls(websocket: WebSocket, db: DbDep, settings: SettingsDep) -> None:
    """Hold a socket open and write every screen pop to it.

    Authenticated by the session cookie, like everything else. Two notes on
    that, both of which cost time to find out the hard way:

    The handshake is a normal HTTP GET, so the cookie is sent — but only
    because the browser opens this socket against its own origin. The Next.js
    dev server proxies `/api/*` to this service precisely so that stays true;
    a socket opened against a different host would be a cross-site request and
    `SameSite=Lax` would strip the cookie.

    And a rejected socket is *closed*, not refused: the handshake has to be
    accepted before a close code can be sent, so a browser that is not signed
    in sees 1008 rather than a 403 it could never have read.
    """
    await websocket.accept()

    principal = await _principal_for(websocket, db, settings)
    if principal is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Sign in to continue.")
        return

    if not principal.can(Permission.CALLS_ASSIST):
        await audit(
            "authz.denied",
            outcome=DENIED,
            principal=principal,
            detail={"required": [Permission.CALLS_ASSIST.value], "transport": "websocket"},
        )
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Your role does not have access to this.",
        )
        return

    hub = get_call_hub()
    async with hub.subscribe(principal) as queue:
        await websocket.send_json({"type": "ready", "watching": hub.watching})
        try:
            while True:
                try:
                    pop = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
                except TimeoutError:
                    # Nothing rang. Say so, so the connection stays open.
                    await websocket.send_json({"type": "ping"})
                    continue
                await websocket.send_json({"type": "call", "pop": pop})
        except WebSocketDisconnect:
            return
        except RuntimeError:
            # The socket closed underneath the send. Ordinary on a page reload.
            return


# --- Live call assistance ---------------------------------------------------


@router.websocket("/assist")
async def assist_call(websocket: WebSocket, db: DbDep, settings: SettingsDep) -> None:
    """Transcript in, suggestions out, for one call.

    The client sends `{"type": "transcript", speaker, text, final}` as words
    arrive, and optionally `{"type": "customer", "customerId": …}` once the
    screen pop has been resolved to an account.

    Nothing in what it sends decides what may be searched. The principal comes
    from the session cookie that opened this socket and is passed to `retrieve`
    unchanged — which matters more here than anywhere else in this application,
    because half the words arriving are spoken by a member of the public.
    """
    await websocket.accept()

    principal = await _principal_for(websocket, db, settings)
    if principal is None or not principal.can(Permission.CALLS_ASSIST):
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Sign in to continue." if principal is None else "Your role has no access.",
        )
        return

    buffer = TranscriptBuffer(settle_seconds=settings.assist_settle_seconds)
    session = _AssistSession(
        websocket=websocket, principal=principal, settings=settings, buffer=buffer
    )
    await session.run()


@dataclass
class _AssistSession:
    """One call's worth of listening.

    A class rather than a loop with six locals because two things run
    concurrently — reading the socket, and a timer that fires when the caller
    stops talking — and they share the buffer between them.
    """

    websocket: WebSocket
    principal: Principal
    settings: Settings
    buffer: TranscriptBuffer
    customer: Any = None
    suggestions: int = 0
    started: float = field(default_factory=time.monotonic)

    async def run(self) -> None:
        await self.websocket.send_json({"type": "ready"})
        watcher = asyncio.create_task(self._watch())
        try:
            while True:
                message = await self.websocket.receive_json()
                await self._handle(message)
        except (WebSocketDisconnect, KeyError, ValueError, TypeError):
            # A disconnect, or a client that sent something that is not the
            # shape agreed above. Neither is worth a stack trace: the socket is
            # closing either way and there is nobody left to tell.
            pass
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
            await self._record()

    async def _handle(self, message: dict[str, Any]) -> None:
        kind = message.get("type")

        if kind == "transcript":
            # Narrowed rather than trusted: this arrives over a socket and
            # anything that is not the employee is the person on the phone.
            speaker: Speaker = "agent" if message.get("speaker") == "agent" else "caller"
            text = str(message.get("text") or "")
            if message.get("final"):
                utterance = self.buffer.add_final(speaker, text)
                if utterance is not None:
                    await self._send(
                        {
                            "type": "utterance",
                            "speaker": utterance.speaker,
                            "text": utterance.text,
                        }
                    )
            else:
                self.buffer.add_partial(speaker, text)
                await self._send({"type": "partial", "speaker": speaker, "text": text})

        elif kind == "customer":
            await self._attach(str(message.get("customerId") or ""))

    async def _attach(self, customer_id: str) -> None:
        """Which account is on the line, once somebody has decided.

        Sent by the client rather than looked up here, because with two accounts
        on one number the answer is a person's judgement — see docs/CALLS.md —
        and the screen pop is where that judgement is made.
        """
        if not customer_id:
            self.customer = None
            return
        try:
            self.customer = await get_crm().get_customer(customer_id)
        except CrmUnavailableError:
            logger.warning("could not attach customer %s to a call", customer_id)
            return
        if self.customer is not None:
            await audit(
                "call.assist.customer",
                principal=self.principal,
                resource_type="customer",
                resource_id=customer_id,
            )

    async def _watch(self) -> None:
        """Fire when the caller stops talking.

        Polling rather than a timer reset on every fragment: a poll at a fifth
        of the settle window is a handful of no-op wakeups a second and cannot
        leak a task, and cancelling and recreating a timer per transcribed word
        can.
        """
        tick = max(self.settings.assist_settle_seconds / 5, 0.05)
        while True:
            await asyncio.sleep(tick)
            if not self.buffer.settled():
                continue
            try:
                await self._consider()
            except Exception:
                # A failure here must not take the transcript down with it. The
                # employee keeps their live transcript and loses a suggestion.
                logger.exception("live assist failed on an utterance")
                await self._send({"type": "assist_error"})

    async def _consider(self) -> None:
        pending = self.buffer.pending()
        context = self.buffer.context()
        newest = self.buffer.latest()
        self.buffer.take()
        if not pending:
            return

        if self.suggestions >= self.settings.assist_max_suggestions:
            # Said once, on the ceiling, rather than silently going quiet.
            if self.suggestions == self.settings.assist_max_suggestions:
                self.suggestions += 1
                await self._send({"type": "capped"})
            return

        llm = get_llm_provider()
        # The window for resolving what was meant, the new lines for deciding
        # whether to act. Deciding from the window would re-answer a problem
        # every time the caller said anything after it.
        verdict = await classify(context, llm, latest=newest)

        if not verdict.actionable:
            # Sent anyway. An assistant that goes quiet is indistinguishable
            # from one that has crashed, and the employee is watching it.
            await self._send({"type": "skipped", "reason": verdict.reason or "not a question"})
            return

        self.suggestions += 1
        await self._send({"type": "thinking", "query": verdict.query, "reason": verdict.reason})

        async with session_scope(self.principal) as db:
            suggestion_id = new_id()
            usage = verdict.usage
            latest = None

            async for suggestion in suggest(
                db,
                query=verdict.query,
                reason=verdict.reason,
                context=context,
                principal=self.principal,
                llm=llm,
                settings=self.settings,
                customer=self.customer,
            ):
                latest = suggestion
                await self._send(_suggestion_out(suggestion_id, suggestion, streaming=True))

            # A terminator, not a nicety. Without it neither the browser nor a
            # test can tell a suggestion that is still arriving from one that
            # has finished, and both end up waiting on a socket that has said
            # everything it is going to say.
            if latest is not None:
                await self._send(_suggestion_out(suggestion_id, latest, streaming=False))

        if latest is not None:
            usage = usage + latest.usage
            await record_usage(
                feature="call_assist",
                provider=llm.name,
                model=llm.chat_model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                cost_usd=cost_usd(llm.chat_model, usage),
                principal=self.principal,
            )

    async def _send(self, message: dict[str, Any]) -> None:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await self.websocket.send_json(message)

    async def _record(self) -> None:
        """One audit line per call, when it ends.

        Per suggestion would be noise — the model calls are already in
        `usage_events`, priced. What the trail wants from a call is that it
        happened, who listened, and how much of it the assistant answered.
        """
        await audit(
            "call.assist",
            principal=self.principal,
            resource_type="customer" if self.customer else None,
            resource_id=self.customer.customer.id if self.customer else None,
            detail={
                "utterances": len(self.buffer.utterances),
                "suggestions": self.suggestions,
                "seconds": round(time.monotonic() - self.started, 1),
            },
        )


def _suggestion_out(
    suggestion_id: str, suggestion: Suggestion, *, streaming: bool
) -> dict[str, Any]:
    """One suggestion on the wire.

    The whole text every time rather than a delta: citation markers are
    resolved server-side against the passages actually retrieved — an invented
    one is *removed* — so a delta would sometimes have to unsay something the
    browser had already drawn.
    """
    answer = suggestion.resolved()
    return {
        "type": "suggestion",
        "id": suggestion_id,
        "query": suggestion.query,
        "reason": suggestion.reason,
        "text": answer.text,
        "streaming": streaming,
        "citations": [_citation_out(citation) for citation in answer.citations],
    }


def _citation_out(citation: Any) -> dict[str, Any]:
    return {
        "marker": citation.marker,
        "documentId": citation.document_id,
        "documentTitle": citation.document_title,
        "page": citation.page,
        "section": citation.section,
        "snippet": citation.snippet,
    }


async def _principal_for(websocket: WebSocket, db: Any, settings: Settings) -> Principal | None:
    token = websocket.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    resolved = await resolve_session(db, token, settings)
    if resolved is None:
        return None
    principal, _session = resolved
    attach_principal(principal)
    return principal
