"""Application configuration.

Validated once at import so a misconfigured deployment fails at boot rather than
on a user's first question.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]

# Repository-relative default for the credential-free path: services/api/data.
_DEFAULT_SQLITE_PATH = Path(__file__).resolve().parent.parent / "data" / "fieldops.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Environment = "development"

    # With demo mode on, the service runs against synthetic data and a local
    # vector store, so the repository is runnable with no accounts at all.
    demo_mode: bool = True

    # Supabase supplies Postgres + pgvector, auth, storage and row-level
    # security. Absent, the service falls back to local SQLite.
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_key: str | None = None
    database_url: str | None = None

    openai_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"

    # "mock" answers from the retrieved passages with no model and no
    # credentials, so the repository is demonstrable end to end on a fresh
    # clone. It is extractive, not generative — see app/llm/mock.py.
    llm_provider: Literal["mock", "openai"] = "mock"

    # Two tiers, on purpose. Query rewriting and intent classification are short
    # and forgiving; paying the answer model to do them is most of a naive RAG
    # system's bill.
    chat_model: str = "gpt-5.6-terra"
    cheap_model: str = "gpt-5.6-luna"

    # --- Connectors ---------------------------------------------------------
    # The mock is a synthetic water-treatment business, so the CRM tools work on
    # a fresh clone. Service Fusion issues credentials to its customers, not to
    # a public repository — see app/connectors/service_fusion.py.
    crm_provider: Literal["mock", "service_fusion"] = "mock"
    service_fusion_client_id: str | None = None
    service_fusion_client_secret: str | None = None

    inventory_provider: Literal["mock", "ply"] = "mock"
    ply_api_key: str | None = None

    # The phone system is the one connector the outside world calls, so its
    # credential is a verification secret rather than an API key. RingCentral
    # hands the token back on every delivery; see app/connectors/telephony.py.
    # The mock generates its own per process, which is why there is no setting
    # for it — a demo secret in a settings file is a secret somebody commits.
    telephony_provider: Literal["mock", "ringcentral"] = "mock"
    ringcentral_verification_token: str | None = None

    # --- Live call assistance -------------------------------------------------
    # How long a pause after an utterance counts as the speaker having finished
    # a thought. Short enough not to be felt, long enough to catch a breath —
    # people talk in fragments, and answering each one separately produces
    # flickering nonsense. See app/realtime/transcript.py.
    assist_settle_seconds: float = Field(default=0.7, ge=0.1, le=5.0)

    # Passages a suggestion is built from. Fewer than a chat answer gets: the
    # employee is reading this with a customer on the line, and a suggestion
    # that needs scrolling has already failed.
    assist_top_k: int = Field(default=3, ge=1, le=10)

    # A ceiling per call. A hold tone transcribed as speech, or a caller reading
    # a manual aloud, would otherwise be an unbounded bill on one phone call.
    assist_max_suggestions: int = Field(default=25, ge=1, le=200)

    # --- Agent --------------------------------------------------------------
    # Where "today" is. An assistant asked for the date has to answer in the
    # business's timezone, not the server's — a container in another region
    # would otherwise be a day ahead for half of every evening. Stated in the
    # system prompt so the model can fill it into a tool call.
    business_timezone: str = "America/New_York"

    # How many times the model may call tools before it must answer. Four is
    # room for a lookup, a follow-up and a correction; it is also a ceiling on
    # what one question can cost when a model gets into a loop.
    agent_max_steps: int = Field(default=4, ge=1, le=10)

    # MCP servers, as JSON: [{"name": …, "command": …, "args": [...]}].
    #
    # The default is `mcp-server-time`, one of the official reference servers,
    # installed as a dependency and launched as a subprocess. It is what answers
    # "what is the date" — a tool this repository did not write, reached over
    # the protocol rather than through a bespoke integration.
    mcp_servers: str = '[{"name": "time", "command": "python", "args": ["-m", "mcp_server_time"]}]'

    # --- Retrieval ----------------------------------------------------------
    # Candidates fetched from each leg of the hybrid search before fusion.
    # Generous, because rank fusion can only reorder what it was given.
    retrieval_candidates: int = 40
    # Passages that survive reranking and reach the prompt.
    retrieval_top_k: int = 6
    # Hard ceiling on retrieved context, in characters. The cheapest token is
    # the one not sent: without a cap, a question that matches a long table
    # quietly costs ten times what a normal one does.
    context_char_budget: int = 12_000

    # cross-encoder — a local ONNX reranker; the largest single quality gain in
    #                 the pipeline, and a ~90 MB download on first use.
    # lexical       — deterministic term-overlap scoring, used by the tests.
    # none          — trust rank fusion alone.
    rerank_provider: Literal["cross-encoder", "lexical", "none"] = "cross-encoder"

    # --- Ingestion ----------------------------------------------------------
    # pdfplumber is MIT and the default. pymupdf is markedly faster and AGPL-3.0,
    # so it is an opt-in extra rather than a dependency this repository imposes.
    pdf_extractor: Literal["pdfplumber", "pymupdf"] = "pdfplumber"

    # Scanned pages carry no text layer. When one is detected the page is routed
    # to OCR instead — "none" leaves it empty and reports it, rather than
    # silently ingesting a blank page as if it were a real one.
    ocr_provider: Literal["none", "paddle"] = "none"

    # A page whose extracted text is shorter than this is treated as having no
    # usable text layer. Manuals have running headers and page numbers, so the
    # threshold has to clear those rather than sit at zero.
    ocr_min_chars_per_page: int = 96

    # Retrieval matches the small chunk and the prompt receives the parent
    # section, so precision and context are tuned separately. Characters, not
    # tokens: the splitter works on text, and one round of conversion is one
    # place for the two numbers to disagree.
    chunk_chars: int = 900
    chunk_overlap_chars: int = 150
    parent_chars: int = 3200

    max_upload_bytes: int = 40 * 1024 * 1024

    # Local ONNX embeddings by default: ingesting a 400-page manual should not
    # cost anything, and it keeps the demo credential-free.
    #
    # "hashing" is a deterministic stand-in used by the test suite. It is not a
    # semantic model and never should be — it exists so the suite stays
    # hermetic and fast instead of downloading 130 MB of ONNX weights.
    embedding_provider: Literal["local", "openai", "hashing"] = "local"

    # One dimension for both providers, so the schema does not change when a
    # deployment switches. The local model (bge-small) is natively 384; OpenAI's
    # text-embedding-3-small is asked for 384 via its `dimensions` parameter,
    # which is what its Matryoshka training makes safe. Changing this number
    # means a migration and a full re-index — it is not a runtime knob.
    embedding_dim: int = 384

    # --- Sessions ---------------------------------------------------------
    session_cookie_name: str = "fieldops_session"
    # Sliding: a session dies this long after its last request.
    session_idle_minutes: int = Field(default=8 * 60, gt=0)
    # Hard ceiling regardless of activity, so a stolen cookie has a shelf life.
    session_absolute_hours: int = Field(default=24 * 14, gt=0)

    # --- Password hashing -------------------------------------------------
    # OWASP's second recommended Argon2id configuration. Exposed as settings
    # rather than hard-coded so the cost can be raised as hardware improves —
    # `needs_rehash` then upgrades each account at its next login — and so the
    # test suite can run at the floor without the application code having to
    # know it is under test.
    argon2_time_cost: int = Field(default=2, gt=0)
    argon2_memory_kib: int = Field(default=19 * 1024, ge=8)
    argon2_parallelism: int = Field(default=1, gt=0)

    # --- Login throttle ---------------------------------------------------
    login_max_attempts: int = Field(default=8, gt=0)
    login_lockout_minutes: int = Field(default=15, gt=0)

    @property
    def sqlalchemy_url(self) -> str:
        """Async driver URL.

        `DATABASE_URL` is accepted in the shape Supabase and Render hand out
        (`postgresql://…`) and rewritten to the async driver, because pasting the
        dashboard string and having it fail on a driver prefix is a bad first
        five minutes.
        """
        if not self.database_url:
            _DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite+aiosqlite:///{_DEFAULT_SQLITE_PATH}"

        url = self.database_url
        for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix) :]
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url[len("postgresql://") :]
        return url

    @property
    def is_postgres(self) -> bool:
        return self.sqlalchemy_url.startswith("postgresql")

    @property
    def vector_store(self) -> Literal["pgvector", "sqlite"]:
        return "pgvector" if self.is_postgres else "sqlite"

    @property
    def cookies_require_https(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def _production_needs_real_infrastructure(self) -> "Settings":
        """Demo defaults are a development convenience. Shipping them to
        production would mean a synthetic corpus and a single-file database on
        an ephemeral disk, so refuse to start instead."""
        if self.environment == "production":
            if self.demo_mode:
                raise ValueError("DEMO_MODE must be false when ENVIRONMENT=production")
            if not self.database_url:
                raise ValueError("DATABASE_URL is required when ENVIRONMENT=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
