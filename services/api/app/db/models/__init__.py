"""ORM models.

Imported as one module so `Base.metadata` is complete before Alembic or
`create_all` looks at it — a model that is never imported is a table that never
gets created, and the failure surfaces much later as a confusing query error.
"""

from app.db.models.chat import ChatMessage, Conversation
from app.db.models.documents import EMBEDDING_DIM, Chunk, Document
from app.db.models.telemetry import AuditEvent, CachedAnswer, UsageEvent
from app.db.models.users import Session, User

__all__ = [
    "EMBEDDING_DIM",
    "AuditEvent",
    "CachedAnswer",
    "ChatMessage",
    "Chunk",
    "Conversation",
    "Document",
    "Session",
    "UsageEvent",
    "User",
]
