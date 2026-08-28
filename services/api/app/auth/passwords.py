"""Password hashing.

Argon2id, via `argon2-cffi`. bcrypt would also be defensible; Argon2id is the
current OWASP first choice and, unlike bcrypt, has no silent 72-byte truncation
of long passphrases.

The parameters below follow OWASP's second recommended configuration (19 MiB,
t=2, p=1) — memory-hard enough to make GPU cracking expensive, cheap enough that
a login is not a noticeable pause.
"""

from __future__ import annotations

from contextlib import suppress
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import get_settings

_DUMMY_PASSWORD = "fieldops-timing-equaliser"


@lru_cache(maxsize=4)
def _build(time_cost: int, memory_cost: int, parallelism: int) -> PasswordHasher:
    return PasswordHasher(
        time_cost=time_cost,
        memory_cost=memory_cost,
        parallelism=parallelism,
        hash_len=32,
        salt_len=16,
    )


def _hasher() -> PasswordHasher:
    settings = get_settings()
    return _build(
        settings.argon2_time_cost, settings.argon2_memory_kib, settings.argon2_parallelism
    )


@lru_cache(maxsize=4)
def _dummy_hash(time_cost: int, memory_cost: int, parallelism: int) -> str:
    """Verified against on unknown emails so that "no such user" and "wrong
    password" cost the same. Without it, response latency enumerates the staff
    list."""
    return _build(time_cost, memory_cost, parallelism).hash(_DUMMY_PASSWORD)


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        burn_time()
        return False
    try:
        return _hasher().verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def burn_time() -> None:
    """Spend roughly one hash's worth of work.

    Called when there is no user to verify against, so the failure path costs the
    same as the success path.
    """
    settings = get_settings()
    dummy = _dummy_hash(
        settings.argon2_time_cost, settings.argon2_memory_kib, settings.argon2_parallelism
    )
    with suppress(VerifyMismatchError):
        _hasher().verify(dummy, "definitely-not-the-password")


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than the current ones.

    Login is the only moment the plaintext exists, so it is the only chance to
    upgrade an old hash in place. Without this, raising the cost parameters only
    protects accounts created afterwards.
    """
    try:
        return _hasher().check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
