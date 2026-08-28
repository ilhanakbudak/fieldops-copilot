"""Request and response bodies.

camelCase on the wire, snake_case in Python: the web app consumes these types
directly and a TypeScript codebase full of `snake_case` field names reads as a
translation of someone else's API rather than its own.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pydantic.alias_generators import to_camel

from app.auth.rbac import Permission, Role


class Schema(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class LoginRequest(Schema):
    email: str
    password: str


class UserSummary(Schema):
    id: str
    email: str
    full_name: str
    role: Role
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class MeResponse(Schema):
    user: UserSummary
    permissions: list[Permission]
    # The document audiences this caller may read. Exposed because the UI says
    # so out loud — an employee who cannot find a document should be able to see
    # that it is a permission boundary, not a broken search.
    document_roles: list[Role]


class CreateUserRequest(Schema):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    # Long enough to survive a wordlist; the length floor does more than a
    # character-class rule, which mostly produces "Password1!".
    password: str = Field(min_length=12, max_length=200)
    role: Role


class UpdateUserRequest(Schema):
    role: Role | None = None
    is_active: bool | None = None


class AuditEventOut(Schema):
    id: str
    occurred_at: datetime
    actor_email: str | None
    actor_role: str | None
    action: str
    outcome: str
    resource_type: str | None
    resource_id: str | None
    request_id: str | None
    ip: str | None
    detail: str | None


class AuditPage(Schema):
    events: list[AuditEventOut]
    total: int


class HealthResponse(Schema):
    status: str
    demo_mode: bool
    vector_store: str
    embeddings: str
    documents: int
