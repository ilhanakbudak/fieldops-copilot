"""The knowledge base: source documents and the chunks retrieval searches.

These were defined a milestone before anything wrote to them, because the
security model depends on their shape and retrofitting role tags onto a corpus
is the kind of migration that gets skipped.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.auth.rbac import Role
from app.config import get_settings
from app.core.ids import new_id
from app.db.base import Base, TimestampMixin
from app.db.types import Embedding, RoleList, UtcDateTime

# Fixed at schema-creation time. Both embedding providers are configured to
# produce this width precisely so the column does not have to change when a
# deployment switches between them.
EMBEDDING_DIM = get_settings().embedding_dim


class Document(Base, TimestampMixin):
    """A manual, SOP, warranty sheet or troubleshooting guide."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # Free-form: "installation-manual", "sop", "warranty", "pricing", "faq".
    # A string rather than an enum because the categories are the business's to
    # decide, and a new one should not need a migration.
    doc_type: Mapped[str] = mapped_column(String(60), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    # Object key in Supabase storage; the UI links to the cited page of the PDF.
    storage_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # SHA-256 of the file, so re-uploading the same PDF is detectable instead of
    # silently doubling the corpus and the retrieval noise.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Which employee audiences may see this. The retrieval query filters on the
    # copy denormalised onto `chunks`; this is the editable source of truth.
    allowed_roles: Mapped[list[Role]] = mapped_column(RoleList, nullable=False)

    # "pending" | "processing" | "ready" | "failed"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    uploaded_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_documents_content_hash"),
        Index("ix_documents_status", "status"),
        Index("ix_documents_doc_type", "doc_type"),
    )


class Chunk(Base):
    """A retrievable passage.

    Two things here are load-bearing for later milestones:

    `allowed_roles` is copied down from the document rather than joined, so the
    role filter is a predicate on the same row the vector index scans. A join
    would work; it would also be the first thing dropped when the query gets
    optimised, and dropping it is a data leak rather than a slowdown.

    `parent_index` records which larger section this chunk belongs to. Retrieval
    matches on the small chunk for precision and then sends the surrounding
    section to the model for context, which is the difference between a
    technically-correct citation and a useful answer.
    """

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    # Ordinal within the document, so a chunk can be located by a human.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 1-indexed, as a reader would count. Null for documents without pages.
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(500), nullable=True)

    embedding: Mapped[list[float] | None] = mapped_column(Embedding(EMBEDDING_DIM), nullable=True)
    allowed_roles: Mapped[list[Role]] = mapped_column(RoleList, nullable=False)

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_id_ordinal"),
        Index("ix_chunks_document_id_parent_index", "document_id", "parent_index"),
    )
