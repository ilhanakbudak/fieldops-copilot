"""Application configuration.

Validated once at import so a misconfigured deployment fails at boot rather than
on a user's first question.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"

    # With demo mode on, the service runs against synthetic data and a local
    # vector store, so the repository is runnable with no accounts at all.
    demo_mode: bool = True

    # Supabase supplies Postgres + pgvector, auth, storage and row-level
    # security. Absent, the service falls back to a local sqlite-vec store.
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_key: str | None = None
    database_url: str | None = None

    openai_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    chat_model: str = "gpt-5.6-terra"

    # Local ONNX embeddings by default: ingesting a 400-page manual should not
    # cost anything, and it keeps the demo credential-free.
    embedding_provider: Literal["local", "openai"] = "local"

    @property
    def vector_store(self) -> Literal["pgvector", "sqlite-vec"]:
        return "pgvector" if self.database_url else "sqlite-vec"


@lru_cache
def get_settings() -> Settings:
    return Settings()
