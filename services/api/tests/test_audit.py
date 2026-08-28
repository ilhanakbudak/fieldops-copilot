"""What ends up on the record.

The easy parts of an audit trail are the successes. The parts that are easy to
get wrong are the failures: a denied request rolls back, and an audit record
written through its transaction would roll back with it.
"""

from __future__ import annotations

import json

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent
from app.db.seed import DEMO_PASSWORD
from tests.conftest import login


async def _events(db: AsyncSession, action: str | None = None) -> list[AuditEvent]:
    query = select(AuditEvent).order_by(AuditEvent.occurred_at)
    if action:
        query = query.where(AuditEvent.action == action)
    return list((await db.execute(query)).scalars())


async def test_a_successful_login_is_recorded(client: AsyncClient, db: AsyncSession) -> None:
    await login(client, "office")

    events = await _events(db, "auth.login")

    assert len(events) == 1
    assert events[0].outcome == "success"
    assert events[0].actor_email == "office@example.com"
    assert events[0].actor_role == "office"
    assert events[0].request_id


async def test_a_failed_login_is_recorded_with_no_actor(
    client: AsyncClient, db: AsyncSession
) -> None:
    """There is no authenticated user — which is exactly why the attempt is
    worth keeping, and why `actor_user_id` is nullable."""
    await client.post("/auth/login", json={"email": "admin@example.com", "password": "wrong"})

    events = await _events(db, "auth.login")

    assert len(events) == 1
    assert events[0].outcome == "denied"
    assert events[0].actor_email == "admin@example.com"
    assert events[0].actor_user_id is None
    assert json.loads(events[0].detail or "{}")["reason"] == "bad_credentials"


async def test_a_login_attempt_on_a_disabled_account_is_recorded(
    client: AsyncClient, db: AsyncSession, second_client: object
) -> None:
    await login(client, "admin")
    users = (await client.get("/admin/users")).json()
    sales = next(user for user in users if user["email"] == "sales@example.com")
    await client.patch(f"/admin/users/{sales['id']}", json={"isActive": False})

    await client.post("/auth/login", json={"email": "sales@example.com", "password": DEMO_PASSWORD})

    denials = [event for event in await _events(db, "auth.login") if event.outcome == "denied"]
    assert json.loads(denials[-1].detail or "{}")["reason"] == "disabled"


async def test_a_denied_request_still_leaves_a_record(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The request that produced this rolled back. The record did not."""
    await login(client, "technician")

    assert (await client.get("/admin/users")).status_code == 403

    denials = await _events(db, "authz.denied")
    assert len(denials) == 1
    assert denials[0].actor_role == "technician"
    assert json.loads(denials[0].detail or "{}")["required"] == ["users:manage"]


async def test_administrative_changes_are_recorded(client: AsyncClient, db: AsyncSession) -> None:
    await login(client, "admin")
    users = (await client.get("/admin/users")).json()
    tech = next(user for user in users if user["email"] == "tech@example.com")

    await client.patch(f"/admin/users/{tech['id']}", json={"role": "office"})
    await client.patch(f"/admin/users/{tech['id']}", json={"isActive": False})

    actions = [event.action for event in await _events(db)]
    assert "user.role_change" in actions
    assert "user.disable" in actions


async def test_one_request_id_ties_a_request_to_its_records(
    client: AsyncClient, db: AsyncSession
) -> None:
    response = await client.post(
        "/auth/login", json={"email": "admin@example.com", "password": DEMO_PASSWORD}
    )

    request_id = response.headers["x-request-id"]
    events = await _events(db, "auth.login")

    assert events[0].request_id == request_id


async def test_an_inbound_request_id_is_honoured_when_it_is_well_formed(
    client: AsyncClient,
) -> None:
    """The Next.js proxy passes one through, so a trace survives the hop."""
    response = await client.get("/health", headers={"x-request-id": "trace-123"})

    assert response.headers["x-request-id"] == "trace-123"


async def test_a_malformed_inbound_request_id_is_replaced(client: AsyncClient) -> None:
    """It reaches log lines and audit rows, so it is only accepted in a shape
    this service controls."""
    response = await client.get("/health", headers={"x-request-id": "bad id\nwith newline"})

    assert response.headers["x-request-id"] != "bad id\nwith newline"


async def test_the_audit_endpoint_is_read_only(client: AsyncClient) -> None:
    """There is no way to edit or delete a row through the API. A trail that can
    be tidied up is not evidence of anything."""
    await login(client, "admin")

    listing = await client.get("/admin/audit")
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1

    event_id = listing.json()["events"][0]["id"]
    assert (await client.delete(f"/admin/audit/{event_id}")).status_code in {404, 405}
    assert (await client.post("/admin/audit", json={})).status_code == 405


async def test_audit_listing_can_be_filtered_and_paged(client: AsyncClient) -> None:
    await login(client, "admin")
    await client.get("/admin/users")

    page = await client.get("/admin/audit", params={"action": "auth.login", "limit": 1})

    assert page.status_code == 200
    assert page.json()["total"] == 1
    assert [event["action"] for event in page.json()["events"]] == ["auth.login"]
