"""Employees and their sessions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.auth.rbac import Role
from app.core.ids import new_id
from app.db.base import Base, TimestampMixin
from app.db.types import UtcDateTime


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # Stored lower-cased, so "J.Smith@…" and "j.smith@…" cannot become two
    # accounts with different permissions.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Argon2id, including its parameters — see app/auth/passwords.py.
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[Role] = mapped_column(String(20), nullable=False)

    # Disabling is a flag rather than a delete: the audit trail references this
    # row, and an audit trail with dangling actors is worth very little.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    disabled_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    """A logged-in browser.

    Server-side sessions rather than a self-contained JWT, because the
    requirement is "ability to disable an employee account" and a JWT stays valid
    until it expires no matter what the database says. Revocation here is a row
    update that the very next request sees.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        # Every authenticated request looks a session up by token hash, so this
        # index is the hot path of the whole API.
        Index("ix_sessions_token_hash", "token_hash", unique=True),
        Index("ix_sessions_user_id_expires_at", "user_id", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Only the SHA-256 of the cookie value. A database dump yields no usable
    # sessions.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    # Sliding idle window; moved forward as the session is used.
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    # Hard ceiling that activity cannot extend, so a stolen cookie expires.
    absolute_expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")
