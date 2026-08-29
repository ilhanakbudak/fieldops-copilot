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


class DocumentSummary(Schema):
    id: str
    title: str
    doc_type: str
    source_filename: str
    page_count: int | None
    chunk_count: int
    allowed_roles: list[Role]
    status: str
    error: str | None
    ingested_at: datetime | None
    created_at: datetime | None


class IngestResponse(Schema):
    document: DocumentSummary
    chunks: int
    pages: int
    # Pages that produced no text and had no OCR to fall through to. Reported
    # rather than swallowed: "ready" on a manual with forty blank pages is a
    # status nobody should trust.
    pages_without_text: list[int]
    duration_ms: int


class RetagRequest(Schema):
    allowed_roles: list[Role] = Field(min_length=1)


class SearchRequest(Schema):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=10, ge=1, le=50)


class SearchHitOut(Schema):
    chunk_id: str
    document_id: str
    document_title: str
    doc_type: str
    content: str
    page: int | None
    section: str | None
    score: float


class SearchResponse(Schema):
    hits: list[SearchHitOut]
    # What the caller was allowed to search. Surfaced so a thin result set is
    # legibly a permission boundary rather than a broken index.
    searched_roles: list[Role]


class CitationOut(Schema):
    marker: str
    chunk_id: str
    document_id: str
    document_title: str
    page: int | None
    section: str | None
    snippet: str


class SourceOut(Schema):
    """A passage that was retrieved, whether or not the answer cites it.

    Sent before the text so the interface can show what the answer is being
    grounded in while it is still being written — and so a reader can see that
    the retrieval was reasonable even when the answer is not.
    """

    marker: str
    document_id: str
    document_title: str
    page: int | None
    section: str | None
    snippet: str
    score: float
    ranks: dict[str, int]


class AskRequest(Schema):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    # Editing a question replaces it and everything after it, rather than
    # branching. A branching thread is a better research tool and a worse
    # working one: the person asking has a customer waiting and wants the
    # corrected answer, not two of them.
    edit_message_id: str | None = None


class ChatMessageOut(Schema):
    id: str
    role: str
    content: str
    citations: list[CitationOut] = []
    created_at: datetime


class ConversationSummary(Schema):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(Schema):
    id: str
    title: str
    messages: list[ChatMessageOut]


class CustomerSummary(Schema):
    id: str
    name: str
    phone: str
    email: str
    address: str
    since: str
    notes: str = ""


class EquipmentOut(Schema):
    id: str
    model: str
    serial: str
    installed_on: str
    location: str
    warranty_until: str | None = None


class JobOut(Schema):
    id: str
    date: str
    kind: str
    summary: str
    technician: str
    notes: str = ""
    status: str


class EstimateOut(Schema):
    id: str
    date: str
    summary: str
    amount_usd: float
    status: str


class InvoiceOut(Schema):
    id: str
    date: str
    amount_usd: float
    balance_usd: float
    status: str


class CustomerDetailOut(Schema):
    customer: CustomerSummary
    equipment: list[EquipmentOut]
    jobs: list[JobOut]
    estimates: list[EstimateOut]
    invoices: list[InvoiceOut]
    outstanding_usd: float


class StockLocationOut(Schema):
    warehouse: str
    aisle: str
    row: str
    bin: str
    on_hand: int
    committed: int
    available: int


class MaterialPricingOut(Schema):
    supplier: str
    supplier_sku: str
    lead_time_days: int
    cost_usd: float
    list_usd: float


class MaterialOut(Schema):
    id: str
    sku: str
    name: str
    category: str
    unit: str
    available: int
    reorder_point: int
    below_reorder: bool
    stock: list[StockLocationOut]
    # Absent, not zero, for a caller without `pricing:read`. A UI that renders a
    # withheld price as $0.00 is worse than one that renders nothing.
    pricing: MaterialPricingOut | None = None


class InboundCallOut(Schema):
    call_id: str
    from_number: str
    to_number: str
    received_at: str


class ScreenPopOut(Schema):
    """What lands on the screen when the phone rings.

    Three shapes in one message, because three things can be true and the
    person answering has to be able to tell them apart at a glance:

    `matches` empty — nobody in the CRM has this number. A new customer, or a
    withheld number. The pop still appears; the number is the useful part.

    `matches` of one, with `detail` filled in — the ordinary case, and the
    whole feature. Equipment, history and notes are on screen before the
    handset reaches an ear.

    `matches` of more than one, `detail` null — two accounts share this number.
    Nothing is opened, because opening the wrong one is worse than opening
    none: a wrong record does not look uncertain while it is being read aloud.
    """

    call: InboundCallOut
    matches: list[CustomerSummary]
    # Trimmed to the last few jobs. A screen pop is read in the seconds before
    # somebody says hello, and a full history is a scroll bar in that window.
    detail: CustomerDetailOut | None = None


class CostBucket(Schema):
    """One row of a cost breakdown — a user, a feature, or a day."""

    label: str
    calls: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    cache_hits: int


class CostSummary(Schema):
    days: int
    calls: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    # Of the input tokens, how many were served from the provider's prompt
    # cache. The number that says whether the prompt is ordered correctly.
    cached_input_tokens: int
    # Turns answered from the semantic cache without calling a model at all.
    cache_hits: int


class CostReport(Schema):
    summary: CostSummary
    by_day: list[CostBucket]
    by_feature: list[CostBucket]
    by_user: list[CostBucket]


class CallDemoNumber(Schema):
    number: str
    label: str


class TranscriptFragment(Schema):
    """One piece of a scripted call, and the pause before it is sent."""

    delay_ms: int
    speaker: str
    text: str
    final: bool


class CallScript(Schema):
    customer_id: str | None = None
    fragments: list[TranscriptFragment]


class CallDemoOut(Schema):
    """Demo mode only. See `app/api/routes/calls.py`."""

    token: str
    header: str
    numbers: list[CallDemoNumber]
    # Replayed by the browser, so the transcript reaches the assist socket the
    # way a transcription service would rather than by a shortcut on the server.
    script: CallScript | None = None


class HealthResponse(Schema):
    status: str
    demo_mode: bool
    vector_store: str
    embeddings: str
    documents: int
