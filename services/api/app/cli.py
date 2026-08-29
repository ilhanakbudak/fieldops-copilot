"""Operational commands.

Deliberately not a web endpoint. Creating the first administrator is something
whoever owns the deployment does from a shell with database access, not
something an unauthenticated HTTP route offers to do — a "create the first
admin" endpoint is a backdoor for exactly as long as someone forgets to remove
it.

    uv run python -m app.cli migrate
    uv run python -m app.cli seed
    uv run python -m app.cli create-user --email … --name … --role admin
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from app.auth.rbac import Role
from app.auth.service import create_user
from app.config import get_settings
from app.db.engine import dispose_engine, session_scope
from app.db.migrate import upgrade_to_head
from app.db.seed import DEMO_PASSWORD, DEMO_USERS, seed_demo_users
from app.rag.corpus import seed_corpus


async def _seed() -> int:
    settings = get_settings()
    if not settings.demo_mode:
        print("Refusing to seed demo accounts outside demo mode.", file=sys.stderr)
        return 1

    async with session_scope() as db:
        created = await seed_demo_users(db)
    # See app/db/engine.py: writing the corpus with no employee signed in is
    # the service identity's job, and Postgres enforces that.
    async with session_scope(service=True) as db:
        documents = await seed_corpus(db)

    print(f"Demo accounts ready ({created} created, {len(DEMO_USERS) - created} already present).")
    for email, name, role in DEMO_USERS:
        print(f"  {role.value:<11} {email:<22} {name}")
    print(f"\nPassword for all of them: {DEMO_PASSWORD}")
    print(f"\nCorpus: {documents} document(s) ingested.")
    return 0


async def _create_user(email: str, name: str, role: str, password: str | None) -> int:
    generated = password is None
    password = password or secrets.token_urlsafe(18)

    async with session_scope() as db:
        user = await create_user(
            db, email=email, full_name=name, password=password, role=Role(role)
        )

    print(f"Created {user.email} ({user.role})")
    if generated:
        print(f"Temporary password: {password}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply all pending migrations")
    sub.add_parser("seed", help="create the demo accounts (demo mode only)")

    create = sub.add_parser("create-user", help="create an employee account")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", required=True, choices=[role.value for role in Role])
    create.add_argument("--password", help="omit to have one generated and printed")

    args = parser.parse_args(argv)

    async def run() -> int:
        try:
            if args.command == "migrate":
                await upgrade_to_head()
                print("Schema is at head.")
                return 0
            if args.command == "seed":
                return await _seed()
            return await _create_user(args.email, args.name, args.role, args.password)
        finally:
            await dispose_engine()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
