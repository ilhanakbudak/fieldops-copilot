"""Administration: employee accounts and the audit trail."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Query, status
from sqlalchemy import case, desc, func, literal_column, select

from app.api.deps import DbDep, PrincipalDep, require
from app.api.schemas import (
    AuditEventOut,
    AuditPage,
    CostBucket,
    CostReport,
    CostSummary,
    CreateUserRequest,
    UpdateUserRequest,
    UserSummary,
)
from app.auth.rbac import Permission, Role
from app.auth.service import (
    count_active_admins,
    create_user,
    get_user,
    list_users,
    set_active,
    set_role,
)
from app.core.clock import utcnow
from app.core.errors import ConflictError
from app.db.models import AuditEvent, UsageEvent, User

router = APIRouter(prefix="/admin", tags=["admin"])

manage_users = [require(Permission.USERS_MANAGE)]


@router.get("/users", response_model=list[UserSummary], dependencies=manage_users)
async def get_users(db: DbDep) -> list[UserSummary]:
    return [UserSummary.model_validate(user) for user in await list_users(db)]


@router.post(
    "/users",
    response_model=UserSummary,
    status_code=status.HTTP_201_CREATED,
    dependencies=manage_users,
)
async def post_user(body: CreateUserRequest, db: DbDep) -> UserSummary:
    user = await create_user(
        db,
        email=str(body.email),
        full_name=body.full_name,
        password=body.password,
        role=body.role,
    )
    return UserSummary.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserSummary, dependencies=manage_users)
async def patch_user(
    user_id: str,
    body: UpdateUserRequest,
    db: DbDep,
    principal: PrincipalDep,
) -> UserSummary:
    """Role and account status.

    Two guards, both of which exist because the alternative is a support call
    nobody can resolve: an administrator cannot disable or demote themselves,
    and the last active administrator cannot be removed. Locking every admin out
    of the system requires database access to undo.
    """
    if user_id == principal.user_id and (body.is_active is False or body.role is not None):
        raise ConflictError("Change another administrator's account, not your own.")

    user = await get_user(db, user_id)
    losing_last_admin = (
        user.role == Role.ADMIN
        and user.is_active
        and (body.is_active is False or (body.role is not None and body.role is not Role.ADMIN))
        and await count_active_admins(db) <= 1
    )
    if losing_last_admin:
        raise ConflictError("The last active administrator cannot be disabled or demoted.")

    if body.role is not None:
        user = await set_role(db, user_id, body.role)
    if body.is_active is not None:
        user = await set_active(db, user_id, active=body.is_active)

    return UserSummary.model_validate(user)


@router.get("/costs", response_model=CostReport, dependencies=[require(Permission.COST_READ)])
async def get_costs(
    db: DbDep,
    days: int = Query(default=30, ge=1, le=365),
) -> CostReport:
    """What the assistant has cost, and where.

    Three breakdowns, because "which feature is expensive" and "who is asking"
    and "is it going up" are three different questions and the first answer to
    each is a different table.

    Every figure is summed from `cost_usd` as it was written, never recomputed
    from tokens against today's price list. Prices change; what a call cost on
    the day it ran does not, and a dashboard that silently rewrites history is
    worse than no dashboard.

    Grouped in SQL rather than in Python: this is the one screen that reads a
    table which grows without bound, and pulling a month of rows into the
    process to sum them is the shape of a page that works until it does not.
    """
    since = utcnow() - timedelta(days=days)
    where = UsageEvent.occurred_at >= since

    totals = (
        await db.execute(
            select(
                func.count().label("calls"),
                func.coalesce(func.sum(UsageEvent.cost_usd), 0.0),
                func.coalesce(func.sum(UsageEvent.input_tokens), 0),
                func.coalesce(func.sum(UsageEvent.output_tokens), 0),
                func.coalesce(func.sum(UsageEvent.cached_input_tokens), 0),
                func.coalesce(func.sum(_hit_flag()), 0),
            ).where(where)
        )
    ).one()

    summary = CostSummary(
        days=days,
        calls=int(totals[0]),
        cost_usd=float(totals[1]),
        input_tokens=int(totals[2]),
        output_tokens=int(totals[3]),
        cached_input_tokens=int(totals[4]),
        cache_hits=int(totals[5]),
    )

    return CostReport(
        summary=summary,
        by_day=await _grouped(db, where, _day_expression(db), order_by_label=True),
        by_feature=await _grouped(db, where, UsageEvent.feature),
        by_user=await _grouped(
            db,
            where,
            func.coalesce(User.email, "(deleted)"),
            join_users=True,
        ),
    )


def _hit_flag() -> Any:
    """`cache_hit` as a number, so it can be summed on both dialects.

    SQLite has no boolean type and stores 0/1; Postgres has one and refuses to
    sum it. A `CASE` is the expression both understand.
    """
    return case((UsageEvent.cache_hit.is_(True), 1), else_=0)


def _day_expression(db: DbDep) -> Any:
    """The calendar day of a usage event, per dialect.

    Two spellings rather than a Python round-trip: grouping a month of rows in
    the process to bucket them by day is the thing this endpoint exists not to
    do.
    """
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    if dialect == "postgresql":
        return func.to_char(UsageEvent.occurred_at, "YYYY-MM-DD")
    return func.strftime("%Y-%m-%d", UsageEvent.occurred_at)


async def _grouped(
    db: DbDep,
    where: Any,
    label: Any,
    *,
    join_users: bool = False,
    order_by_label: bool = False,
) -> list[CostBucket]:
    query = select(
        label.label("label"),
        func.count().label("calls"),
        func.coalesce(func.sum(UsageEvent.input_tokens), 0),
        func.coalesce(func.sum(UsageEvent.output_tokens), 0),
        func.coalesce(func.sum(UsageEvent.cached_input_tokens), 0),
        func.coalesce(func.sum(UsageEvent.cost_usd), 0.0),
        func.coalesce(func.sum(_hit_flag()), 0),
    ).where(where)

    if join_users:
        query = query.select_from(UsageEvent).outerjoin(User, User.id == UsageEvent.user_id)

    query = query.group_by(label)
    # By day reads as a series and wants chronological order; the other two are
    # rankings and want the expensive one first.
    query = query.order_by(label.asc() if order_by_label else desc(literal_column("6")))

    return [
        CostBucket(
            label=str(row[0]),
            calls=int(row[1]),
            input_tokens=int(row[2]),
            output_tokens=int(row[3]),
            cached_input_tokens=int(row[4]),
            cost_usd=float(row[5]),
            cache_hits=int(row[6]),
        )
        for row in (await db.execute(query)).all()
    ]


@router.get("/audit", response_model=AuditPage, dependencies=[require(Permission.AUDIT_READ)])
async def get_audit(
    db: DbDep,
    action: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditPage:
    """Newest first, filterable by action.

    Read-only by construction: there is no endpoint that edits or deletes an
    audit row, because a trail that can be tidied up is not evidence of
    anything.
    """
    where = [AuditEvent.action == action] if action else []

    total = (
        await db.execute(select(func.count()).select_from(AuditEvent).where(*where))
    ).scalar_one()
    rows = (
        await db.execute(
            select(AuditEvent)
            .where(*where)
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()

    return AuditPage(
        events=[AuditEventOut.model_validate(row) for row in rows],
        total=int(total),
    )
