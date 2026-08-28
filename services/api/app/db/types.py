"""Column types that behave on both Postgres and SQLite.

The repository has to run two ways: against Supabase Postgres, which is the real
target, and against a local SQLite file so a reviewer can clone it and get a
running system without an account anywhere. One set of models serves both, and
the differences are confined to this module rather than smeared across the
schema.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Dialect, LargeBinary, String, Text, TypeDecorator
from sqlalchemy.types import TypeEngine

from app.auth.rbac import Role


class UtcDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC on the way in, timezone-aware UTC on the way out.

    Postgres has `timestamptz`. SQLite stores whatever it is handed and returns
    it naive, which silently turns a UTC audit timestamp into a local-time one
    the first time something formats it. This closes that gap.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime reached the database layer")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class RoleList(TypeDecorator[list[Role]]):
    """The set of roles a document is written for.

    Postgres gets a real `text[]`, so the retrieval query can filter with
    `allowed_roles && :caller_roles` and have an index help. SQLite gets JSON,
    which is slower but correct, and the demo corpus is small enough that it
    does not matter.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import ARRAY

            return dialect.type_descriptor(ARRAY(String))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: list[Role] | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        roles = sorted({Role(role).value for role in value})
        return roles if dialect.name == "postgresql" else json.dumps(roles)

    def process_result_value(self, value: Any, dialect: Dialect) -> list[Role] | None:
        if value is None:
            return None
        raw = value if isinstance(value, list) else json.loads(value)
        return [Role(role) for role in raw]


class Embedding(TypeDecorator[list[float]]):
    """A chunk embedding.

    On Postgres this is `pgvector`'s `vector(n)`, indexed with HNSW. On SQLite it
    is the same float32 buffer `sqlite-vec` expects, stored as a blob — the
    ingestion milestone reads it back through the same column either way.
    """

    impl = LargeBinary
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        super().__init__()
        self.dimensions = dimensions

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dimensions))
        return dialect.type_descriptor(LargeBinary())

    def process_bind_param(self, value: list[float] | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if len(value) != self.dimensions:
            raise ValueError(
                f"embedding has {len(value)} dimensions, schema expects {self.dimensions}"
            )
        if dialect.name == "postgresql":
            return value
        import struct

        return struct.pack(f"{self.dimensions}f", *value)

    def process_result_value(self, value: Any, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return list(value)
        import struct

        return list(struct.unpack(f"{self.dimensions}f", value))
