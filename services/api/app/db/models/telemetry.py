"""Audit and cost records.

Both tables are append-only and both are deliberately denormalised: they store
the actor's email and role as they were at the time, not just a foreign key. An
audit line that reads "role: technician" a year after that person was promoted
to admin is the only version of the line that is true.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.base import Base
from app.db.types import UtcDateTime


class AuditEvent(Base):
    """One security-relevant thing that happened.

    Written for authentication, administration, every AI answer and every
    connector call — the last two are why `action` is a dotted string rather
    than an enum: `connector.crm.get_customer` should not need a migration to
    exist.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_actor_user_id_occurred_at", "actor_user_id", "occurred_at"),
        Index("ix_audit_events_action_occurred_at", "action", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    # Null for a failed login: there is no authenticated actor, but the attempt
    # is exactly the event worth recording.
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)

    action: Mapped[str] = mapped_column(String(100), nullable=False)
    # "success" | "denied" | "error"
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)

    resource_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Correlates every event raised while serving one request, including the
    # retrieval and connector calls made on the way.
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)

    # JSON text rather than a JSON column: it is read by humans and by
    # `SELECT … LIMIT 50`, never queried into, and this keeps the column
    # identical on both dialects.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class UsageEvent(Base):
    """One model call, with what it cost.

    Cost is computed and stored at write time rather than derived on read. Prices
    change; what a call cost on the day it ran does not.
    """

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_occurred_at", "occurred_at"),
        Index("ix_usage_events_user_id_occurred_at", "user_id", "occurred_at"),
        Index("ix_usage_events_feature_occurred_at", "feature", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # "chat" | "query_rewrite" | "rerank" | "embedding" | "call_assist" — the
    # dimension the cost dashboard groups by, because "which feature is
    # expensive" is the question that leads to a fix.
    feature: Mapped[str] = mapped_column(String(60), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Prompt-cache hits are billed differently, so they are counted separately
    # rather than folded into the input total.
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True when the semantic cache answered and no model was called at all.
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
