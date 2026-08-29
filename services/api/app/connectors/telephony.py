"""The telephony boundary.

Same shape as the CRM and inventory connectors — a protocol, a vendor adapter
and a mock — and one difference that changes how it has to be built.

**This is the only connector the outside world calls.** Everywhere else the
application decides to go and fetch something, with a signed-in employee behind
the request. Here a telephone system POSTs to us, unauthenticated, at a moment
nobody chose. That inverts the trust: the payload is attacker-controlled until
proven otherwise, and the endpoint has to be useless to anybody who has not
proven it.

Three consequences, all of them in this file rather than in the route:

**Verification is the connector's job.** Each vendor proves itself differently —
RingCentral echoes a token it gave you when the subscription was created; a
different platform signs the body with HMAC. A route that unwrapped a "signature"
header itself would be a route that has to change per vendor, and the point of a
protocol is that it does not.

**Verification is not optional and there is no flag to turn it off.** `verify`
is on the protocol with no default, so an implementation cannot satisfy it by
omission. The mock verifies too — against a token it generates for the demo —
because a mock that accepted everything would mean the credential-free path
exercised a code path production does not have.

**Parsing is separate from verifying, and both come before the CRM.** A payload
that fails either produces nothing at all. No lookup runs, no screen pop is
pushed, and the response body is the same either way — see
`app/api/routes/calls.py` for why that last part matters.

Read-only by construction, like the others: there is no method here that places,
transfers or ends a call. What a caller-ID integration needs from a phone system
is the fact that a phone rang and the number it rang from.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core.clock import utcnow
from app.lib.phone import normalise_phone

logger = logging.getLogger("fieldops.telephony")


@dataclass(frozen=True, slots=True)
class InboundCall:
    """A phone ringing, in our vocabulary rather than a vendor's.

    `from_number` is kept exactly as the platform sent it, because it is what an
    employee will read back to a caller, and `digits` is the normalised form the
    CRM lookup uses. Keeping both is deliberate: a number displayed from its
    normalised form has lost its country code, and a number matched on its
    display form does not match.
    """

    call_id: str
    from_number: str
    to_number: str
    received_at: str

    @property
    def digits(self) -> str:
        return normalise_phone(self.from_number)


class TelephonyUnavailableError(Exception):
    """The phone system could not be reached.

    Present for symmetry with the other connectors and, at the moment, unraised:
    this integration is push-only. It is here because the first thing anybody
    adds to a caller-ID integration is "look up the recording afterwards", and
    that call can fail.
    """


@runtime_checkable
class TelephonyConnector(Protocol):
    name: str

    def verify(self, headers: dict[str, str], body: bytes) -> bool:
        """Is this delivery genuinely from the phone system?

        Takes the raw body, not the parsed payload. An HMAC is over bytes, and
        a signature checked against a re-serialised dictionary is a signature
        check that passes for the wrong reasons.
        """
        ...

    def parse(self, payload: dict[str, Any]) -> InboundCall | None:
        """The call this delivery describes, or `None`.

        `None` is the ordinary case, not an error. Phone platforms deliver every
        state change on a call — ringing, answered, held, ended — and a screen
        pop is wanted for exactly one of them.
        """
        ...


# --- RingCentral ------------------------------------------------------------


class RingCentralConnector:
    """RingCentral telephony session notifications.

    **This adapter has never run against the real API.** RingCentral issues
    credentials to its customers, not to a public repository, so what is
    demonstrable is the shape: the verification token, the event envelope, and
    the translation into `InboundCall`.

    RingCentral's scheme is a shared token rather than a signature. When a
    subscription is created you supply a `verificationToken`; it arrives back in
    the `Verification-Token` header on every delivery. That is weaker than an
    HMAC over the body — it proves the sender knows a secret, not that the body
    is untampered — and the honest thing to do is say so rather than let the
    word "verified" carry more weight than it earns. It is also why the route
    treats a verified payload as *authentic*, never as *authoritative*: the
    number is looked up, and nothing in the payload is written anywhere.
    """

    name = "ringcentral"

    # The delivery that means "a phone is ringing". RingCentral sends the whole
    # lifecycle of a call; `Setup` and `Proceeding` are the two states that
    # happen before anybody picks up, and `Proceeding` is the one that arrives
    # with the caller's number reliably populated.
    RINGING = frozenset({"Setup", "Proceeding"})

    def __init__(self, verification_token: str) -> None:
        if not verification_token:
            raise ValueError("RingCentral requires a verification token")
        self._token = verification_token

    def verify(self, headers: dict[str, str], body: bytes) -> bool:
        # Header names arrive with whatever case the sender used.
        lowered = {key.lower(): value for key, value in headers.items()}
        supplied = lowered.get("verification-token", "")
        # Constant-time, because this is a secret comparison on an endpoint an
        # attacker can call as often as they like.
        return bool(supplied) and hmac.compare_digest(supplied, self._token)

    def parse(self, payload: dict[str, Any]) -> InboundCall | None:
        body = payload.get("body")
        if not isinstance(body, dict):
            return None

        parties = body.get("parties")
        if not isinstance(parties, list):
            return None

        for party in parties:
            if not isinstance(party, dict):
                continue
            if party.get("direction") != "Inbound":
                continue
            status = party.get("status")
            if not isinstance(status, dict) or status.get("code") not in self.RINGING:
                continue

            origin = party.get("from")
            if not isinstance(origin, dict):
                continue
            number = str(origin.get("phoneNumber") or "")
            if not number:
                continue

            destination = party.get("to")
            dialled = destination.get("phoneNumber") if isinstance(destination, dict) else ""

            return InboundCall(
                call_id=str(body.get("telephonySessionId") or body.get("sessionId") or ""),
                from_number=number,
                to_number=str(dialled or ""),
                received_at=str(payload.get("timestamp") or utcnow().isoformat()),
            )

        return None


# --- Mock -------------------------------------------------------------------


class MockTelephonyConnector:
    """A phone system for a repository that does not have one.

    It verifies. The token is generated once per process and printed by the
    health endpoint in demo mode, so the demo page can post a call to itself
    without a credential in a file — and so the verification branch that
    production depends on is the same branch the demo runs.

    Its payload shape is deliberately not RingCentral's. A mock that mimicked
    the vendor's envelope would make the adapter look like a formality; two
    genuinely different shapes behind one protocol is the thing being
    demonstrated.
    """

    name = "mock"

    def __init__(self, token: str | None = None) -> None:
        self._token = token or secrets.token_urlsafe(24)

    @property
    def token(self) -> str:
        return self._token

    def verify(self, headers: dict[str, str], body: bytes) -> bool:
        lowered = {key.lower(): value for key, value in headers.items()}
        supplied = lowered.get("x-fieldops-call-token", "")
        return bool(supplied) and hmac.compare_digest(supplied, self._token)

    def parse(self, payload: dict[str, Any]) -> InboundCall | None:
        if payload.get("event") != "call.ringing":
            return None
        number = str(payload.get("from") or "")
        if not number:
            return None
        return InboundCall(
            call_id=str(payload.get("callId") or secrets.token_hex(8)),
            from_number=number,
            to_number=str(payload.get("to") or ""),
            received_at=str(payload.get("at") or utcnow().isoformat()),
        )
