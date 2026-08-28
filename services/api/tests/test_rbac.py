"""The authorisation boundary, asserted as a matrix.

The permission table in `app/auth/rbac.py` is the whole authorisation model.
These tests assert the table itself, and then assert that the HTTP surface
actually consults it — a correct table nobody checks is decoration.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.auth.rbac import Permission, Role, permissions_for
from tests.conftest import login

# Read this as the specification. A change to the model has to change this
# table, which is the point: the boundary cannot move quietly.
MATRIX: dict[Permission, set[Role]] = {
    Permission.DOCUMENTS_READ: {Role.ADMIN, Role.OFFICE, Role.SALES, Role.TECHNICIAN},
    Permission.CUSTOMERS_READ: {Role.ADMIN, Role.OFFICE, Role.SALES, Role.TECHNICIAN},
    Permission.INVENTORY_READ: {Role.ADMIN, Role.OFFICE, Role.SALES, Role.TECHNICIAN},
    # A technician quoting from the margin sheet on a customer's driveway is the
    # scenario this row exists to prevent.
    Permission.PRICING_READ: {Role.ADMIN, Role.SALES},
    # Answering the phone is an office job.
    Permission.CALLS_ASSIST: {Role.ADMIN, Role.OFFICE},
    Permission.DOCUMENTS_MANAGE: {Role.ADMIN},
    Permission.USERS_MANAGE: {Role.ADMIN},
    Permission.AUDIT_READ: {Role.ADMIN},
    Permission.COST_READ: {Role.ADMIN},
}


@pytest.mark.parametrize("permission", list(Permission))
@pytest.mark.parametrize("role", list(Role))
def test_permission_matrix(role: Role, permission: Permission) -> None:
    expected = role in MATRIX[permission]

    assert (permission in permissions_for(role)) is expected


def test_every_permission_is_covered() -> None:
    """A new permission with no row here would otherwise be untested — and,
    since the matrix is parametrised over `Permission`, silently so."""
    assert set(MATRIX) == set(Permission)


def test_a_technician_sees_only_technician_documents() -> None:
    from app.auth.rbac import document_roles_for

    assert document_roles_for(Role.TECHNICIAN) == {Role.TECHNICIAN}
    assert document_roles_for(Role.ADMIN) == set(Role)


@pytest.mark.parametrize("role", ["office", "sales", "technician"])
async def test_non_admins_cannot_reach_administration(client: AsyncClient, role: str) -> None:
    await login(client, role)

    for method, path in (
        ("GET", "/admin/users"),
        ("GET", "/admin/audit"),
        ("POST", "/admin/users"),
    ):
        response = await client.request(method, path, json={})
        assert response.status_code == 403, f"{role} reached {method} {path}"
        assert response.json()["error"]["code"] == "forbidden"


async def test_an_anonymous_caller_is_challenged_not_forbidden(client: AsyncClient) -> None:
    """401 and 403 mean different things to the web app: one sends the user to
    the login page, the other tells them their role is not enough."""
    response = await client.get("/admin/users")

    assert response.status_code == 401


async def test_an_admin_can_reach_administration(client: AsyncClient) -> None:
    await login(client, "admin")

    response = await client.get("/admin/users")

    assert response.status_code == 200
    assert {user["email"] for user in response.json()} == {
        "admin@example.com",
        "office@example.com",
        "sales@example.com",
        "tech@example.com",
    }
