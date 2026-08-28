"""Applying migrations from Python.

Alembic's command API is synchronous and its environment opens its own event
loop, so it runs in a worker thread. Wrapping it here means the CLI, the tests
and the demo-mode boot path all migrate the same way — one behaviour to be
right about.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic.config import Config

from alembic import command

# services/api — the directory alembic.ini and alembic/ live in.
_ROOT = Path(__file__).resolve().parent.parent.parent


def _config() -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "alembic"))
    return config


def upgrade_to_head_sync() -> None:
    command.upgrade(_config(), "head")


def downgrade_to_base_sync() -> None:
    command.downgrade(_config(), "base")


async def upgrade_to_head() -> None:
    await asyncio.to_thread(upgrade_to_head_sync)


async def downgrade_to_base() -> None:
    """Unwind every migration.

    Used to reset the Postgres test database between runs, which has the useful
    side effect that the downgrade path is exercised rather than assumed — the
    half of a migration nobody runs until the day they urgently need it.
    """
    await asyncio.to_thread(downgrade_to_base_sync)
