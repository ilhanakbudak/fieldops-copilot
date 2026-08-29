"""Is the database arranged the way the security model assumes?

One check, and it exists because of a specific way this deployment can be wrong
while looking entirely right.

`documents`, `chunks`, `audit_events` and `usage_events` carry `FORCE ROW LEVEL
SECURITY`, and docs/SECURITY.md calls those policies the backstop: the thing that
still refuses a restricted chunk if a query in the retrieval layer ever forgets
its role filter. That claim depends on a fact about the *connecting role* that
nothing in the schema can express — a role with `BYPASSRLS` is not subject to any
policy, ever, and neither the migration nor the policies can tell.

Supabase hands out exactly such a role. The connection string on a new project's
dashboard is for `postgres`, which is not a superuser there but does carry
`BYPASSRLS`. Connect with it and every policy in migration 0002 is inert. The
application behaves identically, the tests pass, the dashboard shows the policies
in place, and the backstop is not there.

So it is checked at boot, out loud, and in production it is fatal. A security
control that is silently absent is worse than one that was never claimed: the
first gets relied upon.

The fix is the arrangement CI already uses — a dedicated role that owns the
schema and has no bypass. See infra/README.md.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings

logger = logging.getLogger("fieldops.db")

_ADVICE = (
    "Row-level security is not protecting this deployment: the connected role "
    "bypasses it. The policies in migration 0002 are inert, and the role filter "
    "in the retrieval layer is the only thing left. Connect as a dedicated "
    "application role that owns the schema and holds neither SUPERUSER nor "
    "BYPASSRLS — see infra/README.md."
)


async def check_rls_backstop(engine: AsyncEngine, settings: Settings) -> bool:
    """True when policies apply to this connection.

    Never raises on its own account. A database that cannot answer the question
    — an ancient server, a pooler that hides `pg_roles` — is reported and
    allowed through, because refusing to boot over a failed *diagnostic* is a
    worse failure than the one it was looking for.
    """
    if not settings.is_postgres:
        # SQLite has no policies to bypass. The local fallback filters in the
        # query only, which docs/SECURITY.md states plainly.
        return True

    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls, current_user "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).first()
    except Exception:
        logger.warning("could not check the row-level security backstop", exc_info=True)
        return True

    if row is None:
        logger.warning("could not identify the connected role; assuming policies apply")
        return True

    is_superuser, bypasses, role = bool(row[0]), bool(row[1]), row[2]
    if not (is_superuser or bypasses):
        logger.info("row-level security applies to %s", role)
        return True

    reason = "is a superuser" if is_superuser else "has BYPASSRLS"
    message = f"the role '{role}' {reason}. {_ADVICE}"

    if settings.environment == "production":
        raise RuntimeError(message)

    logger.warning("%s", message)
    return False
