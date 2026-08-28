"""Server-side sessions.

A logged-in browser holds an opaque random token in an HttpOnly cookie. The
database holds only its SHA-256, plus two expiry timestamps:

- `expires_at` slides forward as the session is used, so an employee working a
  full shift is not logged out mid-question.
- `absolute_expires_at` does not, so a cookie copied off a machine stops working
  whether or not it keeps getting used.

Chosen over a JWT deliberately. Someone leaves, or an account is compromised,
and access has to stop now — but a self-contained token stays valid until it
expires no matter what the database says, which turns "disable this account"
into "disable this account within fifteen minutes". Here it is a row update the
next request sees.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Principal, Role
from app.config import Settings
from app.core.clock import utcnow
from app.core.ids import hash_token, new_id, new_token
from app.db.models import Session, User


async def create_session(
    db: AsyncSession,
    user: User,
    settings: Settings,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[Session, str]:
    """Returns the row and the plaintext token — the only moment it exists."""
    now = utcnow()
    token = new_token()
    session = Session(
        id=new_id(),
        user_id=user.id,
        token_hash=hash_token(token),
        created_at=now,
        expires_at=now + timedelta(minutes=settings.session_idle_minutes),
        absolute_expires_at=now + timedelta(hours=settings.session_absolute_hours),
        last_seen_at=now,
        ip=ip,
        user_agent=user_agent[:400] if user_agent else None,
    )
    db.add(session)
    await db.flush()
    return session, token


async def resolve_session(
    db: AsyncSession, token: str, settings: Settings
) -> tuple[Principal, Session] | None:
    """Turn a cookie into a caller, or into nothing.

    Every reason to refuse returns the same `None`: expired, revoked, unknown
    token, or a user who has since been disabled. The caller cannot tell which,
    and does not need to.
    """
    row = (
        await db.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_hash == hash_token(token))
        )
    ).first()
    if row is None:
        return None

    session, user = row
    now = utcnow()
    if (
        session.revoked_at is not None
        or session.expires_at <= now
        or session.absolute_expires_at <= now
        # Checked on every request rather than only at login: disabling an
        # account has to take effect immediately, not at the next login.
        or not user.is_active
    ):
        return None

    # Slide the idle window, but never past the absolute ceiling.
    session.expires_at = min(
        now + timedelta(minutes=settings.session_idle_minutes),
        session.absolute_expires_at,
    )
    session.last_seen_at = now

    principal = Principal(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=Role(user.role),
        session_id=session.id,
    )
    return principal, session


async def revoke_session(db: AsyncSession, session_id: str) -> None:
    await db.execute(
        update(Session)
        .where(Session.id == session_id, Session.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def revoke_all_for_user(db: AsyncSession, user_id: str) -> int:
    """Used when an account is disabled or its role changes.

    A role change has to invalidate sessions too — otherwise a demoted employee
    keeps their old permissions for as long as their browser stays open, because
    the principal is built from the row as it was when the session resolved.
    """
    result = cast(
        "CursorResult[Any]",
        await db.execute(
            update(Session)
            .where(Session.user_id == user_id, Session.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        ),
    )
    return int(result.rowcount or 0)
