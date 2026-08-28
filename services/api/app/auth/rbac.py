"""Roles, permissions, and the caller they describe.

The business has four kinds of employee and they do not see the same things: a
technician has no reason to read margin or supplier pricing, and a salesperson
has no reason to disable accounts.

Two rules shape this module:

1. **Permissions are data, not scattered `if role == "admin"` checks.** One table
   below is the whole authorisation model, which means it can be read in one
   sitting and asserted against in one test.
2. **Roles reach the retrieval layer as a filter, not as a post-check.** Which
   is why `Principal` carries `document_roles` — the set of document audiences
   this caller may see. Filtering after retrieval would let a restricted chunk
   into the model's context, and therefore into its answer, even if the UI never
   rendered it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    OFFICE = "office"
    SALES = "sales"
    TECHNICIAN = "technician"


class Permission(StrEnum):
    """Verbs, not screens. A permission survives a UI redesign."""

    DOCUMENTS_READ = "documents:read"
    DOCUMENTS_MANAGE = "documents:manage"
    CUSTOMERS_READ = "customers:read"
    INVENTORY_READ = "inventory:read"
    PRICING_READ = "pricing:read"
    CALLS_ASSIST = "calls:assist"
    USERS_MANAGE = "users:manage"
    AUDIT_READ = "audit:read"
    COST_READ = "cost:read"


_TECHNICIAN = frozenset(
    {
        Permission.DOCUMENTS_READ,
        Permission.CUSTOMERS_READ,
        Permission.INVENTORY_READ,
    }
)

# Sales needs pricing; it does not need the live call assistant, which is an
# office-staff tool.
_SALES = _TECHNICIAN | {Permission.PRICING_READ}

# Office staff answer the phone, so they get call assist and customer history.
_OFFICE = _TECHNICIAN | {Permission.CALLS_ASSIST}

_ADMIN = frozenset(Permission)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: _ADMIN,
    Role.OFFICE: frozenset(_OFFICE),
    Role.SALES: frozenset(_SALES),
    Role.TECHNICIAN: _TECHNICIAN,
}


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]


def document_roles_for(role: Role) -> frozenset[Role]:
    """Which document audiences a role may read.

    Documents are tagged with the roles they are written for. An admin reads
    everything; everyone else reads exactly their own audience. Kept as a
    function rather than an implicit "role == tag" comparison because the day a
    supervisor role needs to see both technician and office material, this is
    the only place that changes.
    """
    if role is Role.ADMIN:
        return frozenset(Role)
    return frozenset({role})


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, as the rest of the application sees them.

    Deliberately not the ORM `User`: nothing downstream should be able to reach
    a password hash, and a frozen value object can be passed into the retrieval
    layer without dragging a database session along with it.
    """

    user_id: str
    email: str
    full_name: str
    role: Role
    session_id: str

    @property
    def permissions(self) -> frozenset[Permission]:
        return permissions_for(self.role)

    @property
    def document_roles(self) -> frozenset[Role]:
        return document_roles_for(self.role)

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions
