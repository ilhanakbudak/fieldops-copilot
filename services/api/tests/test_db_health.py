"""The row-level security backstop check.

The case worth testing is the one a real deployment hits and CI cannot: a role
that bypasses every policy while the schema still shows the policies in place.
So the role is faked here rather than created — creating a `BYPASSRLS` role
requires privileges the test database's own role does not have, which is the
point of that database's role.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings
from app.db.health import check_rls_backstop


class _Engine:
    """Enough of an engine to answer one query."""

    def __init__(self, row: tuple[Any, ...] | None, *, raises: bool = False) -> None:
        self._row = row
        self._raises = raises

    def connect(self) -> Any:
        engine = self

        class _Connection:
            async def __aenter__(self) -> Any:
                if engine._raises:
                    raise RuntimeError("the pooler hid pg_roles")
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def execute(self, *_: object) -> Any:
                class _Result:
                    def first(self) -> tuple[Any, ...] | None:
                        return engine._row

                return _Result()

        return _Connection()


def _settings(environment: str) -> Settings:
    return Settings(environment=environment, demo_mode=False, database_url="postgresql://x/y")


async def test_an_ordinary_role_is_protected() -> None:
    engine = _Engine((False, False, "fieldops_app"))

    assert await check_rls_backstop(engine, _settings("development"))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("row", "why"),
    [
        ((False, True, "postgres"), "Supabase's own role carries BYPASSRLS"),
        ((True, False, "postgres"), "a superuser is not subject to any policy"),
    ],
)
async def test_a_bypassing_role_is_reported(row: tuple[Any, ...], why: str) -> None:
    engine = _Engine(row)

    assert not await check_rls_backstop(engine, _settings("development")), why


async def test_it_refuses_to_boot_in_production() -> None:
    """A security control that is silently absent is worse than one that was
    never claimed: the first gets relied upon."""
    engine = _Engine((False, True, "postgres"))

    with pytest.raises(RuntimeError, match="BYPASSRLS"):
        await check_rls_backstop(engine, _settings("production"))


async def test_a_failed_diagnostic_is_not_a_failed_boot() -> None:
    """Refusing to start over a broken *check* is a worse failure than the one
    it was looking for."""
    engine = _Engine(None, raises=True)

    assert await check_rls_backstop(engine, _settings("production"))  # type: ignore[arg-type]


async def test_sqlite_has_no_policies_to_bypass() -> None:
    settings = Settings(environment="development", demo_mode=True)

    assert await check_rls_backstop(None, settings)  # type: ignore[arg-type]
