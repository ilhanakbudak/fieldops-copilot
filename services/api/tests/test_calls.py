"""Caller lookup: the webhook, the lookup, and who gets to watch.

The webhook is the only route in this application an unauthenticated stranger is
meant to reach, so most of what is asserted here is about what it refuses and
what it declines to reveal.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Permission, Principal, Role, permissions_for
from app.connectors import MockCrmConnector, MockTelephonyConnector, get_telephony
from app.connectors.telephony import RingCentralConnector
from app.core.errors import AuthorizationError
from app.core.ids import new_id
from app.db.models import AuditEvent
from app.realtime.hub import CallHub
from tests.conftest import login

KNOWN = "(207) 555-0233"  # Priya Raman, one account
SHARED = "(207) 555-0177"  # Two Okonkwo accounts on one line
UNKNOWN = "(207) 555-0000"


def _payload(number: str, event: str = "call.ringing") -> dict[str, Any]:
    return {"event": event, "from": number, "to": "(207) 555-0100", "callId": "CALL-1"}


async def _ring(client: AsyncClient, number: str, **kwargs: Any) -> Any:
    connector = get_telephony()
    assert isinstance(connector, MockTelephonyConnector)
    return await client.post(
        "/calls/incoming",
        json=_payload(number, **kwargs),
        headers={"X-FieldOps-Call-Token": connector.token},
    )


async def _pops(db: AsyncSession) -> list[dict[str, Any]]:
    events = list(
        (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.action == "call.incoming")
                .order_by(AuditEvent.occurred_at)
            )
        ).scalars()
    )
    return [json.loads(event.detail or "{}") for event in events]


class TestTheWebhook:
    async def test_a_known_number_finds_the_one_account(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        response = await _ring(client, KNOWN)

        assert response.status_code == 204
        assert [pop["matched"] for pop in await _pops(db)] == [["NG-0876"]]

    async def test_a_shared_number_finds_both_accounts(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """The case the screen pop exists to get right. Two accounts on one
        line means the person answering chooses; opening the first is a wrong
        record being read aloud, and a wrong record never looks uncertain."""
        await _ring(client, SHARED)

        matched = (await _pops(db))[0]["matched"]
        assert sorted(matched) == ["NG-1203", "NG-1288"]

    async def test_an_unknown_number_still_produces_a_call(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """Not an error. It is how a new customer rings for the first time, and
        the number is the useful part of the pop."""
        await _ring(client, UNKNOWN)

        pop = (await _pops(db))[0]
        assert pop["matched"] == []
        assert pop["from"] == "2075550000"

    async def test_the_number_is_normalised_before_the_lookup(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """The phone system says +1207…, the CRM holds (207) 555-…."""
        await _ring(client, "+12075550233")

        assert (await _pops(db))[0]["matched"] == ["NG-0876"]

    async def test_an_unverified_delivery_is_refused_and_recorded(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        response = await client.post(
            "/calls/incoming",
            json=_payload(KNOWN),
            headers={"X-FieldOps-Call-Token": "not-the-token"},
        )

        assert response.status_code == 204
        assert await _pops(db) == [], "an unverified delivery reached the CRM"

        denied = (
            await db.execute(select(AuditEvent).where(AuditEvent.action == "call.webhook"))
        ).scalar_one()
        assert denied.outcome == "denied"

    async def test_a_delivery_with_no_token_is_refused(self, client: AsyncClient) -> None:
        response = await client.post("/calls/incoming", json=_payload(KNOWN))

        assert response.status_code == 204

    async def test_a_refusal_is_indistinguishable_from_a_lookup(self, client: AsyncClient) -> None:
        """The whole reason this endpoint always answers 204.

        A status that varied would let an unauthenticated caller ask, at
        whatever rate they liked, which numbers belong to this business's
        customers — and would turn token guessing into a game with feedback.
        """
        known = await _ring(client, KNOWN)
        unknown = await _ring(client, UNKNOWN)
        forged = await client.post("/calls/incoming", json=_payload(KNOWN))
        garbage = await client.post(
            "/calls/incoming",
            content=b"not json at all",
            headers={"X-FieldOps-Call-Token": "wrong"},
        )

        statuses = {known.status_code, unknown.status_code, forged.status_code}
        assert statuses == {204}
        assert garbage.status_code == 204
        assert known.content == unknown.content == forged.content == b""

    async def test_a_delivery_that_is_not_a_ringing_call_is_ignored(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """Phone systems deliver every state change on a call. One of them is
        worth a screen pop."""
        await _ring(client, KNOWN, event="call.ended")

        assert await _pops(db) == []


class TestWhoMayWatch:
    """`calls:assist` is office staff and administrators. A technician on a
    driveway has no reason to be shown who is ringing the office, and the phone
    numbers and addresses in a screen pop are exactly the sort of thing that
    should not be broadcast further than it needs to go.
    """

    @pytest.mark.parametrize(
        ("role", "allowed"),
        [
            (Role.ADMIN, True),
            (Role.OFFICE, True),
            (Role.SALES, False),
            (Role.TECHNICIAN, False),
        ],
    )
    def test_the_permission_table_says_so(self, role: Role, allowed: bool) -> None:
        assert (Permission.CALLS_ASSIST in permissions_for(role)) is allowed

    async def test_the_hub_refuses_a_role_that_may_not_assist(self) -> None:
        """Asserted on the hub itself, not only on the route. This is the
        object that does the sending, and a fan-out that will send to whatever
        is in its list is one careless `subscribe()` from a leak."""
        hub = CallHub()
        technician = Principal(
            user_id=new_id(),
            email="tech@example.com",
            full_name="Tech",
            role=Role.TECHNICIAN,
            session_id=new_id(),
        )

        with pytest.raises(AuthorizationError):
            async with hub.subscribe(technician):
                pass

        assert hub.watching == 0

    async def test_a_subscriber_leaves_when_its_block_ends(self) -> None:
        hub = CallHub()
        office = Principal(
            user_id=new_id(),
            email="office@example.com",
            full_name="Office",
            role=Role.OFFICE,
            session_id=new_id(),
        )

        async with hub.subscribe(office):
            assert hub.watching == 1
        assert hub.watching == 0

    async def test_a_full_queue_costs_one_subscriber_and_not_the_others(self) -> None:
        """A screen pop is worth having in the first second and worthless in
        the thirtieth, so a browser that has stopped reading is dropped rather
        than allowed to hold up the office."""
        hub = CallHub()

        def _principal(email: str) -> Principal:
            return Principal(
                user_id=new_id(),
                email=email,
                full_name=email,
                role=Role.OFFICE,
                session_id=new_id(),
            )

        async with (
            hub.subscribe(_principal("slow@example.com")) as slow,
            hub.subscribe(_principal("fast@example.com")) as fast,
        ):
            for _ in range(slow.maxsize):
                hub.publish({"filling": "the queue"})
            while not fast.empty():
                fast.get_nowait()

            assert hub.publish({"one": "more"}) == 1
            assert fast.get_nowait() == {"one": "more"}


class TestTheDemoEndpoint:
    async def test_it_hands_the_office_what_it_needs_to_ring_the_phone(
        self, client: AsyncClient
    ) -> None:
        await login(client, "office")
        response = await client.get("/calls/demo")

        assert response.status_code == 200
        body = response.json()
        connector = get_telephony()
        assert isinstance(connector, MockTelephonyConnector)
        assert body["token"] == connector.token
        assert len(body["numbers"]) == 3

    async def test_a_technician_may_not_have_the_token(self, client: AsyncClient) -> None:
        """It is a demo secret and it is still a secret: anybody holding it can
        forge a screen pop."""
        await login(client, "technician")

        assert (await client.get("/calls/demo")).status_code == 403

    async def test_it_is_not_available_without_a_session(self, client: AsyncClient) -> None:
        assert (await client.get("/calls/demo")).status_code == 401


class TestTheRingCentralAdapter:
    """The adapter has never run against the real API. What can be asserted is
    that it reads RingCentral's envelope and refuses everything else."""

    def _connector(self) -> RingCentralConnector:
        return RingCentralConnector("the-verification-token")

    def test_it_verifies_on_the_token_ringcentral_sends_back(self) -> None:
        connector = self._connector()

        assert connector.verify({"Verification-Token": "the-verification-token"}, b"{}")
        assert not connector.verify({"Verification-Token": "another"}, b"{}")
        assert not connector.verify({}, b"{}")

    def test_the_header_name_is_matched_case_insensitively(self) -> None:
        assert self._connector().verify({"verification-token": "the-verification-token"}, b"{}")

    def test_it_reads_an_inbound_ringing_party(self) -> None:
        call = self._connector().parse(
            {
                "timestamp": "2026-08-29T10:00:00Z",
                "body": {
                    "telephonySessionId": "s-1",
                    "parties": [
                        {
                            "direction": "Inbound",
                            "status": {"code": "Proceeding"},
                            "from": {"phoneNumber": "+12075550233"},
                            "to": {"phoneNumber": "+12075550100"},
                        }
                    ],
                },
            }
        )

        assert call is not None
        assert call.from_number == "+12075550233"
        assert call.digits == "2075550233"
        assert call.call_id == "s-1"

    def test_an_outbound_call_is_not_a_screen_pop(self) -> None:
        assert (
            self._connector().parse(
                {
                    "body": {
                        "parties": [
                            {
                                "direction": "Outbound",
                                "status": {"code": "Proceeding"},
                                "from": {"phoneNumber": "+12075550100"},
                            }
                        ]
                    }
                }
            )
            is None
        )

    def test_an_answered_call_is_not_a_screen_pop(self) -> None:
        """The pop belongs in the seconds before somebody picks up. After that
        it is a distraction on a screen already being used."""
        assert (
            self._connector().parse(
                {
                    "body": {
                        "parties": [
                            {
                                "direction": "Inbound",
                                "status": {"code": "Answered"},
                                "from": {"phoneNumber": "+12075550233"},
                            }
                        ]
                    }
                }
            )
            is None
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"body": None},
            {"body": {}},
            {"body": {"parties": "not a list"}},
            {"body": {"parties": [None]}},
            {"body": {"parties": [{"direction": "Inbound", "status": {"code": "Proceeding"}}]}},
        ],
    )
    def test_a_malformed_delivery_produces_nothing_rather_than_an_exception(
        self, payload: dict[str, Any]
    ) -> None:
        """This is the one connector a stranger can post to. A `KeyError` here
        is a 500 in the logs at best, and a way to tell verified payloads from
        unverified ones by timing at worst."""
        assert self._connector().parse(payload) is None

    def test_it_will_not_start_without_a_token(self) -> None:
        with pytest.raises(ValueError, match="verification token"):
            RingCentralConnector("")


class TestTheConnectorIsReadOnly:
    def test_there_is_no_way_to_place_or_end_a_call(self) -> None:
        """Structural, like the CRM adapter's. The protocol has two methods and
        neither of them does anything to a telephone."""
        surface = {
            name
            for name in dir(RingCentralConnector)
            if not name.startswith("_") and callable(getattr(RingCentralConnector, name))
        }

        assert surface == {"verify", "parse"}


async def test_a_number_shared_by_two_accounts_is_in_the_fixtures() -> None:
    """The fixture that makes the ambiguity reproducible. If this ever becomes
    a single match, the multiple-match path stops being tested and nothing
    else says so."""
    crm = MockCrmConnector()

    assert len(await crm.find_by_phone(SHARED)) == 2


class TestTheOfficesSocket:
    """The WebSocket, over a real handshake.

    Synchronous, because `TestClient` is the only client in the stack that
    speaks the protocol, and it drives the ASGI app on its own loop. That is
    also why these tests seed nothing: they are about who gets to hold the
    socket open, which is settled before any data moves.
    """

    def _client(self) -> Any:
        from starlette.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def _sign_in(self, client: Any, role: str) -> None:
        from app.db.seed import DEMO_PASSWORD

        email = {"technician": "tech@example.com"}.get(role, f"{role}@example.com")
        response = client.post("/auth/login", json={"email": email, "password": DEMO_PASSWORD})
        assert response.status_code == 200, response.text

    def test_office_staff_may_watch(self) -> None:
        with self._client() as client:
            self._sign_in(client, "office")
            with client.websocket_connect("/calls/stream") as socket:
                assert socket.receive_json() == {"type": "ready", "watching": 1}

    def test_a_technician_is_closed_rather_than_refused(self) -> None:
        """A WebSocket handshake has to be accepted before a close code can be
        sent, so the refusal arrives as 1008 rather than as a 403 the browser
        could never have read."""
        from starlette.testclient import WebSocketDisconnect as Disconnected

        with self._client() as client:
            self._sign_in(client, "technician")
            with (
                pytest.raises(Disconnected) as refused,
                client.websocket_connect("/calls/stream") as socket,
            ):
                socket.receive_json()

        assert refused.value.code == 1008

    def test_an_unauthenticated_socket_is_closed(self) -> None:
        from starlette.testclient import WebSocketDisconnect as Disconnected

        with (
            self._client() as client,
            pytest.raises(Disconnected) as refused,
            client.websocket_connect("/calls/stream") as socket,
        ):
            socket.receive_json()

        assert refused.value.code == 1008

    def test_a_call_reaches_the_screen(self) -> None:
        """End to end: the webhook arrives on one connection, the pop comes out
        of a socket held open on another."""
        with self._client() as client:
            self._sign_in(client, "office")
            with client.websocket_connect("/calls/stream") as socket:
                assert socket.receive_json()["type"] == "ready"

                connector = get_telephony()
                assert isinstance(connector, MockTelephonyConnector)
                posted = client.post(
                    "/calls/incoming",
                    json=_payload(KNOWN),
                    headers={"X-FieldOps-Call-Token": connector.token},
                )
                assert posted.status_code == 204

                message = socket.receive_json()

        assert message["type"] == "call"
        pop = message["pop"]
        assert [match["id"] for match in pop["matches"]] == ["NG-0876"]
        assert pop["detail"]["customer"]["name"] == "Priya Raman"
        assert pop["call"]["fromNumber"] == KNOWN

    def test_two_accounts_on_one_number_open_neither(self) -> None:
        with self._client() as client:
            self._sign_in(client, "office")
            with client.websocket_connect("/calls/stream") as socket:
                socket.receive_json()
                connector = get_telephony()
                assert isinstance(connector, MockTelephonyConnector)
                client.post(
                    "/calls/incoming",
                    json=_payload(SHARED),
                    headers={"X-FieldOps-Call-Token": connector.token},
                )
                pop = socket.receive_json()["pop"]

        assert len(pop["matches"]) == 2
        assert pop["detail"] is None, "a record was opened for an ambiguous number"

    def test_an_unknown_caller_still_reaches_the_screen(self) -> None:
        with self._client() as client:
            self._sign_in(client, "office")
            with client.websocket_connect("/calls/stream") as socket:
                socket.receive_json()
                connector = get_telephony()
                assert isinstance(connector, MockTelephonyConnector)
                client.post(
                    "/calls/incoming",
                    json=_payload(UNKNOWN),
                    headers={"X-FieldOps-Call-Token": connector.token},
                )
                pop = socket.receive_json()["pop"]

        assert pop["matches"] == []
        assert pop["detail"] is None
        assert pop["call"]["fromNumber"] == UNKNOWN
