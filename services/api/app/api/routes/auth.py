"""Sign in, sign out, and who am I."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.deps import DbDep, OptionalPrincipalDep, PrincipalDep, SettingsDep
from app.api.middleware import client_ip
from app.api.schemas import LoginRequest, MeResponse, UserSummary
from app.audit import audit
from app.auth.service import authenticate, get_user
from app.auth.sessions import revoke_session
from app.config import Settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    """HttpOnly so a cross-site script cannot read it. SameSite=Lax so a form
    on another site cannot ride the session. Secure in production — omitted in
    development only because localhost is not served over HTTPS."""
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookies_require_https,
        samesite="lax",
        path="/",
        max_age=settings.session_absolute_hours * 3600,
    )


@router.post("/login", response_model=MeResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
) -> MeResponse:
    principal, _session, token = await authenticate(
        db,
        settings,
        email=body.email,
        password=body.password,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token, settings)

    user = await get_user(db, principal.user_id)
    return MeResponse(
        user=UserSummary.model_validate(user),
        permissions=sorted(principal.permissions),
        document_roles=sorted(principal.document_roles),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    principal: OptionalPrincipalDep,
) -> Response:
    """Idempotent: signing out of an already-dead session is not an error, it is
    the outcome the caller wanted."""
    if principal is not None:
        await revoke_session(db, principal.session_id)
        await audit("auth.logout", principal=principal)

    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookies_require_https,
        samesite="lax",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse)
async def me(principal: PrincipalDep, db: DbDep) -> MeResponse:
    user = await get_user(db, principal.user_id)
    return MeResponse(
        user=UserSummary.model_validate(user),
        permissions=sorted(principal.permissions),
        document_roles=sorted(principal.document_roles),
    )
