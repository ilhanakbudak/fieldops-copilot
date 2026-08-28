"""Demo accounts.

Four employees, one per role, so the authorisation boundaries can be walked
through rather than described. The passwords are printed and identical because
these accounts exist to be logged into by a stranger reading the repository —
which is also why seeding refuses to run outside demo mode.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.auth.rbac import Role
from app.core.ids import new_id
from app.db.models import User

# Synthetic, and obviously so: `example.com` is the reserved documentation
# domain, so nothing here can be mistaken for a real employee's address.
DEMO_PASSWORD = "demo-password-1234"

DEMO_USERS: list[tuple[str, str, Role]] = [
    ("admin@example.com", "Dana Okafor", Role.ADMIN),
    ("office@example.com", "Marta Reyes", Role.OFFICE),
    ("sales@example.com", "Chris Lindqvist", Role.SALES),
    ("tech@example.com", "Sam Whitfield", Role.TECHNICIAN),
]


async def seed_demo_users(db: AsyncSession) -> int:
    """Insert any demo account that is missing. Returns how many were created.

    Idempotent, because it runs on every boot in demo mode and a restart should
    not be a failure — nor should it reset a password someone has changed.
    """
    created = 0
    for email, full_name, role in DEMO_USERS:
        exists = (
            await db.execute(select(func.count()).select_from(User).where(User.email == email))
        ).scalar_one()
        if exists:
            continue
        db.add(
            User(
                id=new_id(),
                email=email,
                full_name=full_name,
                password_hash=hash_password(DEMO_PASSWORD),
                role=role,
                is_active=True,
            )
        )
        created += 1

    if created:
        await db.flush()
    return created
