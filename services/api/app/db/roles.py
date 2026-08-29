"""How a role filter is spelled, per dialect.

`documents.allowed_roles` and `chunks.allowed_roles` hold the audiences a row is
written for, and every query that touches the corpus has to intersect them with
the caller's. That predicate is the single most load-bearing line of SQL in the
application, and before this module it was written out four times — twice in
SQL and twice as a list comprehension in Python.

The Python two were the problem. Filtering after the rows are in memory is
correct today and is one careless refactor away from not being: the restricted
row has already been read, so leaking it is an edit to the line *below* the one
anybody would think to check. Both are now predicates, which means a query that
forgets the filter returns nothing rather than everything.

Two forms, because the corpus is queried both ways:

- `visible_to()` for ORM and Core selects.
- `visible_to_sql()` for the hand-written `text()` queries in the vector and
  keyword stores, where the ranking expression has to sit in the same statement.

On Postgres both compile to `allowed_roles && ARRAY[…]`, which the GIN index in
migration 0002 serves. On SQLite the column is a JSON array and the overlap is a
set of `LIKE`s — slower, and still in the query.

Row-level security backs all of this up on Postgres (see docs/SECURITY.md).
These predicates are what make the queries fast and readable; the policies are
what make them true.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, String, Text, false, or_, type_coerce
from sqlalchemy.orm import InstrumentedAttribute

from app.auth.rbac import Role


def audience_key(roles: frozenset[Role]) -> str:
    """The caller's document audience, as one comparable string.

    Used by the semantic cache, where the audience is part of the key rather
    than a filter applied to the result — an answer built from a technician's
    documents must not be served to a salesperson. Sorted and joined rather
    than hashed, so an operator reading that table can see the boundary working
    without a lookup.
    """
    return ",".join(sorted(role.value for role in roles))


def visible_to(
    column: InstrumentedAttribute[list[Role]], roles: frozenset[Role], dialect: str
) -> ColumnElement[bool]:
    """A boolean expression: does this row's audience include one of `roles`?"""
    wanted = sorted(role.value for role in roles)
    if not wanted:
        # An empty audience matches nothing. Spelled out rather than left to
        # `or_()` of nothing, which SQLAlchemy renders as true.
        return false()

    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import ARRAY

        # The column is a `RoleList` type decorator; coercing to the underlying
        # array type is what makes the `&&` operator available.
        overlap: ColumnElement[bool] = type_coerce(column, ARRAY(String)).overlap(wanted)
        return overlap

    # Coerced to `Text` so the `LIKE` pattern is bound as a string. Without it
    # the parameter is handed to `RoleList.process_bind_param`, which expects a
    # list of roles and JSON-encodes whatever it is given.
    stored = type_coerce(column, Text())
    return or_(*(stored.like(f'%"{value}"%') for value in wanted))


def visible_to_sql(
    column: str, roles: frozenset[Role], dialect: str, *, prefix: str = "role"
) -> tuple[str, dict[str, object]]:
    """The same predicate as a SQL fragment, for the stores' `text()` queries.

    Returns the fragment and the parameters it expects, so the caller can drop
    it into a `WHERE` clause alongside a distance ordering without the filter
    and the ranking ending up in two different statements.
    """
    wanted = sorted(role.value for role in roles)
    if not wanted:
        return "1 = 0", {}

    if dialect == "postgresql":
        return f"{column} && :{prefix}s", {f"{prefix}s": wanted}

    clauses = " OR ".join(f"{column} LIKE :{prefix}{index}" for index in range(len(wanted)))
    params: dict[str, object] = {
        f"{prefix}{index}": f'%"{value}"%' for index, value in enumerate(wanted)
    }
    return f"({clauses})", params
