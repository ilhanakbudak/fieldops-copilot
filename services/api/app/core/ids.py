"""Identifiers and secrets.

Primary keys are UUIDv4 rather than sequential integers: the ids appear in URLs
and audit records, and a sequence leaks how many customers, documents and users
exist.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid


def new_id() -> str:
    return str(uuid.uuid4())


def new_token(byte_length: int = 32) -> str:
    """A session token. 32 bytes of `secrets` entropy, URL-safe."""
    return secrets.token_urlsafe(byte_length)


def hash_token(token: str) -> str:
    """Store this, never the token itself.

    A stolen database dump then contains no usable sessions. SHA-256 rather than
    Argon2 is deliberate — the input is 256 bits of random, so there is no
    dictionary to slow down, and this runs on every authenticated request.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
