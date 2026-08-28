"""Authentication and account administration.

Route handlers stay thin: they translate HTTP, this module decides.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import DENIED, audit
from app.auth.passwords import burn_time, hash_password, needs_rehash, verify_password
from app.auth.rbac import Principal, Role
from app.auth.sessions import create_session, revoke_all_for_user
from app.auth.throttle import LoginThrottle
from app.config import Settings
from app.core.clock import utcnow
from app.core.errors import AuthenticationError, ConflictError, NotFoundError, RateLimitedError
from app.core.ids import new_id
from app.db.models import Session, User

_throttle: LoginThrottle | None = None


def get_throttle(settings: Settings) -> LoginThrottle:
    global _throttle
    if _throttle is None:
        _throttle = LoginThrottle(
            max_attempts=settings.login_max_attempts,
            lockout=timedelta(minutes=settings.login_lockout_minutes),
        )
    return _throttle


def reset_throttle() -> None:
    global _throttle
    _throttle = None


def normalise_email(email: str) -> str:
    return email.strip().lower()


async def authenticate(
    db: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> tuple[Principal, Session, str]:
    email = normalise_email(email)
    throttle = get_throttle(settings)
    keys = [f"email:{email}", f"ip:{ip or 'unknown'}"]

    retry_after = throttle.retry_after(keys)
    if retry_after is not None:
        await audit("auth.login", outcome=DENIED, actor_email=email, detail={"reason": "throttled"})
        raise RateLimitedError(
            "Too many failed attempts. Try again shortly.",
            detail={"retryAfterSeconds": retry_after},
        )

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

    # A disabled account and a wrong password produce the same response and,
    # because the hash still runs, roughly the same latency. Telling an attacker
    # which of the two it was is free intelligence about the staff list.
    if user is None:
        burn_time()
        ok = False
    else:
        ok = user.is_active and verify_password(password, user.password_hash)

    if not ok:
        throttle.record_failure(keys)
        await audit(
            "auth.login",
            outcome=DENIED,
            actor_email=email,
            resource_type="user",
            resource_id=user.id if user else None,
            detail={"reason": "disabled" if user and not user.is_active else "bad_credentials"},
        )
        raise AuthenticationError("Incorrect email or password.")

    assert user is not None
    throttle.record_success(keys)

    # The only moment the plaintext exists is the only chance to upgrade a hash
    # written under weaker parameters.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    user.last_login_at = utcnow()
    session, token = await create_session(db, user, settings, ip=ip, user_agent=user_agent)

    principal = Principal(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=Role(user.role),
        session_id=session.id,
    )
    await audit("auth.login", principal=principal, resource_type="user", resource_id=user.id)
    return principal, session, token


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    full_name: str,
    password: str,
    role: Role,
) -> User:
    email = normalise_email(email)
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("An account with that email already exists.")

    user = User(
        id=new_id(),
        email=email,
        full_name=full_name.strip(),
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await audit(
        "user.create",
        resource_type="user",
        resource_id=user.id,
        detail={"email": email, "role": role.value},
    )
    return user


async def get_user(db: AsyncSession, user_id: str) -> User:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise NotFoundError("No such user.")
    return user


async def list_users(db: AsyncSession) -> list[User]:
    result = await db.execute(select(User).order_by(User.full_name))
    return list(result.scalars())


async def set_active(db: AsyncSession, user_id: str, *, active: bool) -> User:
    """Disable or re-enable an employee.

    Disabling revokes their live sessions in the same transaction rather than
    waiting for the next request to notice the flag. Both halves matter: the
    flag stops new logins, the revocation stops the browser already open on the
    warehouse floor.
    """
    user = await get_user(db, user_id)
    if user.is_active == active:
        return user

    user.is_active = active
    user.disabled_at = None if active else utcnow()

    revoked = 0
    if not active:
        revoked = await revoke_all_for_user(db, user_id)

    await audit(
        "user.enable" if active else "user.disable",
        resource_type="user",
        resource_id=user_id,
        detail={"email": user.email, "sessionsRevoked": revoked},
    )
    return user


async def set_role(db: AsyncSession, user_id: str, role: Role) -> User:
    """Change an employee's role.

    Live sessions are revoked because the principal — and therefore which
    documents retrieval will consider — is built when the session resolves. A
    demotion that only applies at next login is not a demotion.
    """
    user = await get_user(db, user_id)
    if user.role == role:
        return user

    previous = user.role
    user.role = role
    revoked = await revoke_all_for_user(db, user_id)

    await audit(
        "user.role_change",
        resource_type="user",
        resource_id=user_id,
        detail={"from": previous, "to": role.value, "sessionsRevoked": revoked},
    )
    return user


async def count_active_admins(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(User)
        .where(User.role == Role.ADMIN, User.is_active.is_(True))
    )
    return int(result.scalar_one())
