"""Loading the synthetic corpus.

`fixtures/corpus/*.md` carries its own front matter — title, type, and the roles
it is written for. Keeping that beside the text rather than in a seed script
means the access-control demo is editable by whoever edits the document, and
adding a fixture is one file rather than two.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Role
from app.config import Settings, get_settings
from app.db.models import Document
from app.rag.embed import EmbeddingProvider
from app.rag.ingest import ingest_document

logger = logging.getLogger("fieldops.rag.corpus")

# app/rag/corpus.py → services/api → repository root → fixtures/corpus
CORPUS_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "corpus"

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True, slots=True)
class FixtureDocument:
    path: Path
    title: str
    doc_type: str
    allowed_roles: list[Role]
    body: str


def parse_fixture(path: Path) -> FixtureDocument:
    raw = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(raw)
    if not match:
        raise ValueError(f"{path.name}: missing front matter")

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()

    roles = [
        Role(item.strip())
        for item in fields.get("allowed_roles", "").strip("[]").split(",")
        if item.strip()
    ]
    if not roles:
        raise ValueError(f"{path.name}: allowed_roles is required")

    return FixtureDocument(
        path=path,
        title=fields.get("title", path.stem),
        doc_type=fields.get("doc_type", "document"),
        allowed_roles=roles,
        # The body keeps its heading structure; only the front matter is
        # stripped, so what is chunked is what a reader would see.
        body=raw[match.end() :],
    )


def load_fixtures(directory: Path | None = None) -> list[FixtureDocument]:
    source = directory or CORPUS_DIR
    if not source.is_dir():
        return []
    return [parse_fixture(path) for path in sorted(source.glob("*.md"))]


async def seed_corpus(
    db: AsyncSession,
    *,
    settings: Settings | None = None,
    embedder: EmbeddingProvider | None = None,
    directory: Path | None = None,
) -> int:
    """Ingest any fixture that is not already present. Returns how many.

    Idempotent through the content hash, so a restart is not a failure and does
    not double the corpus.
    """
    settings = settings or get_settings()
    existing = int((await db.execute(select(func.count()).select_from(Document))).scalar_one())

    loaded = 0
    for fixture in load_fixtures(directory):
        data = fixture.body.encode("utf-8")
        try:
            await ingest_document(
                db,
                data=data,
                filename=fixture.path.name,
                title=fixture.title,
                doc_type=fixture.doc_type,
                allowed_roles=fixture.allowed_roles,
                settings=settings,
                embedder=embedder,
            )
            loaded += 1
        except Exception as error:
            # Already-ingested files raise a conflict on the content hash; that
            # is the idempotency working, not a problem worth shouting about.
            if existing:
                logger.debug("skipping %s: %s", fixture.path.name, error)
            else:
                logger.warning("could not ingest %s: %s", fixture.path.name, error)

    return loaded
