"""Request dependencies.

Three of them, layered: a database session, the caller, and a permission gate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Cookie, Depends, Request, params
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import DENIED, audit
from app.audit.log import flush
from app.auth.rbac import Permission, Principal
from app.auth.sessions import resolve_session
from app.config import Settings, get_settings
from app.core.context import attach_principal
from app.core.errors import AuthenticationError, AuthorizationError
from app.db.engine import apply_principal, get_sessionmaker

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_db() -> AsyncIterator[AsyncSession]:
    """A transaction per request, committed if the handler returns.

    Handlers do not commit. A handler that returns having half-written its
    changes is a bug; a handler that raises should leave nothing behind.
    """
    try:
        async with get_sessionmaker()() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    finally:
        # Once, and only once, the connection is back in the pool. Audit records
        # are written outside the caller's transaction on purpose — a denied
        # request rolls back, and the denial still has to be on the record — but
        # writing them while it is still open deadlocks SQLite against itself.
        await flush()


DbDep = Annotated[AsyncSession, Depends(get_db)]


async def optional_principal(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    fieldops_session: Annotated[str | None, Cookie()] = None,
) -> Principal | None:
    if not fieldops_session:
        return None

    resolved = await resolve_session(db, fieldops_session, settings)
    if resolved is None:
        return None

    principal, _session = resolved
    # Published to the request context so audit records raised deeper in the
    # stack — retrieval, connectors — know who asked, without being passed a
    # request object.
    attach_principal(principal)
    request.state.principal = principal

    # Postgres row-level security reads these; see app/db/engine.py.
    await apply_principal(db, principal)
    return principal


OptionalPrincipalDep = Annotated[Principal | None, Depends(optional_principal)]


async def current_principal(principal: OptionalPrincipalDep) -> Principal:
    if principal is None:
        raise AuthenticationError("Sign in to continue.")
    return principal


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def require(*permissions: Permission) -> params.Depends:
    """Gate a route on permissions the caller must hold — all of them.

    Written for the route decorator's `dependencies=[…]` rather than as a
    handler argument, so the gate is declared next to the path and cannot be
    quietly dropped by someone tidying up an unused parameter.

    Denials are audited. A refusal nobody records is indistinguishable from an
    attempt that never happened, and "who tried to open the pricing documents"
    is precisely the question an audit trail exists to answer.
    """

    async def _dependency(principal: PrincipalDep) -> Principal:
        missing = [p for p in permissions if not principal.can(p)]
        if missing:
            await audit(
                "authz.denied",
                outcome=DENIED,
                principal=principal,
                detail={"required": [p.value for p in missing]},
            )
            raise AuthorizationError("Your role does not have access to this.")
        return principal

    return params.Depends(dependency=_dependency)
