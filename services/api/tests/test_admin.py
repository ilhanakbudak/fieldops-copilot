"""Account administration, and the guards around it."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from httpx import AsyncClient

from app.db.seed import DEMO_PASSWORD
from tests.conftest import login

NewClient = Callable[[], AbstractAsyncContextManager[AsyncClient]]


async def _user_id(client: AsyncClient, email: str) -> str:
    users = (await client.get("/admin/users")).json()
    return next(user["id"] for user in users if user["email"] == email)


async def test_creating_an_account_lets_it_sign_in(
    client: AsyncClient, second_client: NewClient
) -> None:
    await login(client, "admin")

    created = await client.post(
        "/admin/users",
        json={
            "email": "New.Hire@example.com",
            "fullName": "Priya Raman",
            "password": "a-sufficiently-long-password",
            "role": "technician",
        },
    )
    assert created.status_code == 201
    assert created.json()["email"] == "new.hire@example.com"

    async with second_client() as fresh:
        signed_in = await fresh.post(
            "/auth/login",
            json={"email": "new.hire@example.com", "password": "a-sufficiently-long-password"},
        )
    assert signed_in.status_code == 200


async def test_duplicate_emails_are_refused(client: AsyncClient) -> None:
    await login(client, "admin")

    response = await client.post(
        "/admin/users",
        json={
            "email": "admin@example.com",
            "fullName": "Someone Else",
            "password": "a-sufficiently-long-password",
            "role": "office",
        },
    )

    assert response.status_code == 409


async def test_short_passwords_are_refused(client: AsyncClient) -> None:
    await login(client, "admin")

    response = await client.post(
        "/admin/users",
        json={
            "email": "short@example.com",
            "fullName": "Short Password",
            "password": "hunter2",
            "role": "office",
        },
    )

    assert response.status_code == 422


async def test_disabling_an_account_blocks_a_new_login(
    client: AsyncClient, second_client: NewClient
) -> None:
    await login(client, "admin")
    user_id = await _user_id(client, "sales@example.com")

    patched = await client.patch(f"/admin/users/{user_id}", json={"isActive": False})
    assert patched.status_code == 200
    assert patched.json()["isActive"] is False

    async with second_client() as fresh:
        blocked = await fresh.post(
            "/auth/login", json={"email": "sales@example.com", "password": DEMO_PASSWORD}
        )

    assert blocked.status_code == 401


async def test_a_role_change_revokes_the_old_session(
    client: AsyncClient, second_client: NewClient
) -> None:
    """The principal — and so which documents retrieval will consider — is built
    when the session resolves. A demotion that waits for the next login is not a
    demotion."""
    async with second_client() as sales:
        await login(sales, "sales")
        assert (await sales.get("/auth/me")).json()["user"]["role"] == "sales"

        await login(client, "admin")
        user_id = await _user_id(client, "sales@example.com")
        await client.patch(f"/admin/users/{user_id}", json={"role": "technician"})

        assert (await sales.get("/auth/me")).status_code == 401


async def test_an_admin_cannot_disable_themselves(client: AsyncClient) -> None:
    await login(client, "admin")
    user_id = await _user_id(client, "admin@example.com")

    response = await client.patch(f"/admin/users/{user_id}", json={"isActive": False})

    assert response.status_code == 409


async def test_the_last_admin_cannot_be_demoted(
    client: AsyncClient, second_client: NewClient
) -> None:
    """Locking every administrator out of the system should require database
    access to undo, so it is refused here instead."""
    await login(client, "admin")
    second = await client.post(
        "/admin/users",
        json={
            "email": "second.admin@example.com",
            "fullName": "Ade Balogun",
            "password": "a-sufficiently-long-password",
            "role": "admin",
        },
    )
    assert second.status_code == 201

    # Two admins now, so demoting one is allowed.
    first = await _user_id(client, "admin@example.com")
    async with second_client() as other:
        await other.post(
            "/auth/login",
            json={
                "email": "second.admin@example.com",
                "password": "a-sufficiently-long-password",
            },
        )
        demoted = await other.patch(f"/admin/users/{first}", json={"role": "office"})
        assert demoted.status_code == 200

        # Now there is one left, and it cannot be removed.
        last = await _user_id(other, "second.admin@example.com")
        assert (
            await other.patch(f"/admin/users/{last}", json={"role": "office"})
        ).status_code == 409
