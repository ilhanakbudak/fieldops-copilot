"""Administration: employee accounts and the audit trail."""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

from app.api.deps import DbDep, PrincipalDep, require
from app.api.schemas import (
    AuditEventOut,
    AuditPage,
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
from app.core.errors import ConflictError
from app.db.models import AuditEvent

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
