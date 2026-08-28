"""Sign-in, sessions, and the ways a session stops working."""

from __future__ import annotations

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.core.clock import utcnow
from app.core.ids import hash_token
from app.db.models import Session, User
from app.db.seed import DEMO_PASSWORD
from tests.conftest import login


async def test_login_sets_an_httponly_cookie(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login", json={"email": "admin@example.com", "password": DEMO_PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "admin@example.com"

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie.replace("samesite", "SameSite")


async def test_email_is_matched_case_insensitively(client: AsyncClient) -> None:
    """Otherwise `Admin@Example.com` is a login failure for the right password,
    and eventually a second account with different permissions."""
    response = await client.post(
        "/auth/login", json={"email": "  Admin@Example.COM ", "password": DEMO_PASSWORD}
    )

    assert response.status_code == 200


async def test_wrong_password_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_unknown_and_known_emails_fail_identically(client: AsyncClient) -> None:
    """The two responses must be indistinguishable, or the login form becomes a
    way to enumerate who works at the company."""
    unknown = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "wrong"}
    )
    known = await client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )

    assert unknown.status_code == known.status_code == 401
    assert unknown.json() == known.json()


async def test_me_requires_a_session(client: AsyncClient) -> None:
    response = await client.get("/auth/me")

    assert response.status_code == 401


async def test_me_reports_role_permissions_and_document_audiences(client: AsyncClient) -> None:
    await login(client, "technician")

    body = (await client.get("/auth/me")).json()

    assert body["user"]["role"] == "technician"
    assert "documents:read" in body["permissions"]
    assert "pricing:read" not in body["permissions"]
    assert body["documentRoles"] == ["technician"]


async def test_logout_revokes_the_session(client: AsyncClient) -> None:
    await login(client, "admin")

    assert (await client.post("/auth/logout")).status_code == 204
    assert (await client.get("/auth/me")).status_code == 401


async def test_logout_without_a_session_is_not_an_error(client: AsyncClient) -> None:
    assert (await client.post("/auth/logout")).status_code == 204


async def test_only_the_token_hash_is_stored(client: AsyncClient) -> None:
    """A database dump must not contain anything that can be replayed as a
    session."""
    from app.db.engine import get_sessionmaker

    await login(client, "admin")
    token = client.cookies[get_settings().session_cookie_name]

    async with get_sessionmaker()() as db:
        stored = list((await db.execute(select(Session.token_hash))).scalars())

    assert token not in stored
    assert hash_token(token) in stored


async def test_an_expired_session_stops_working(client: AsyncClient) -> None:
    from app.db.engine import get_sessionmaker

    await login(client, "admin")
    assert (await client.get("/auth/me")).status_code == 200

    async with get_sessionmaker()() as db:
        session = (await db.execute(select(Session))).scalars().one()
        session.expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    assert (await client.get("/auth/me")).status_code == 401


async def test_activity_cannot_push_a_session_past_its_absolute_ceiling(
    client: AsyncClient,
) -> None:
    """The idle window slides; the hard ceiling does not. A cookie lifted off a
    machine and kept warm must still die."""
    from app.db.engine import get_sessionmaker

    await login(client, "admin")

    async with get_sessionmaker()() as db:
        session = (await db.execute(select(Session))).scalars().one()
        ceiling = utcnow() + timedelta(minutes=1)
        session.absolute_expires_at = ceiling
        await db.commit()

    assert (await client.get("/auth/me")).status_code == 200

    async with get_sessionmaker()() as db:
        session = (await db.execute(select(Session))).scalars().one()
        assert session.expires_at == ceiling


async def test_disabling_an_account_kills_its_live_session(client: AsyncClient) -> None:
    """Someone leaves and access has to stop now. A flag that only takes effect
    at the next login does not do that."""
    from app.db.engine import get_sessionmaker

    await login(client, "technician")
    assert (await client.get("/auth/me")).status_code == 200

    async with get_sessionmaker()() as db:
        user = (
            (await db.execute(select(User).where(User.email == "tech@example.com"))).scalars().one()
        )
        user.is_active = False
        await db.commit()

    assert (await client.get("/auth/me")).status_code == 401


async def test_a_garbage_cookie_is_treated_as_no_session(client: AsyncClient) -> None:
    client.cookies.set(get_settings().session_cookie_name, "not-a-real-token")

    assert (await client.get("/auth/me")).status_code == 401


async def test_repeated_failures_are_eventually_throttled(client: AsyncClient) -> None:
    """Argon2 makes offline cracking expensive; only throttling makes online
    guessing expensive."""
    settings = get_settings()
    last = None
    for _ in range(settings.login_max_attempts + 1):
        last = await client.post(
            "/auth/login", json={"email": "admin@example.com", "password": "wrong"}
        )

    assert last is not None
    assert last.status_code == 429
    assert last.json()["error"]["retryAfterSeconds"] > 0
