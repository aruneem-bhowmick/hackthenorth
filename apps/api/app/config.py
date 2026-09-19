from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Real process env vars always win (Docker Compose sets those directly); this
# is a fallback for local/non-Docker runs, where cwd is apps/api and a bare
# ".env" would miss the repo-root file. Idempotent no-op if it doesn't exist
# (e.g. inside the container, where this resolves near filesystem root).
_REPO_ROOT_ENV = Path(__file__).resolve().parent.parent.parent.parent / ".env"


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment / .env (see .env.example)."""

    model_config = SettingsConfigDict(env_file=(".env", _REPO_ROOT_ENV), extra="ignore")

    database_url: str = "postgresql+asyncpg://pincite:pincite@localhost:5432/pincite"
    redis_url: str = "redis://localhost:6379/0"
    uploads_dir: str = "./data/uploads"
    max_upload_bytes: int = 25 * 1024 * 1024  # FR-ING-001: 25 MB cap
    # Comma-separated browser origins permitted to call this API. Keep the
    # local UI as the safe default; production adds its exact Vercel domain.
    allowed_origins: str = "http://localhost:3000"

    sentry_dsn_api: str | None = None
    sentry_environment: str = "development"

    courtlistener_api_token: str | None = None
    openai_api_key: str | None = None
    elastic_cloud_id: str | None = None
    elastic_api_key: str | None = None
    browserbase_api_key: str | None = None
    browserbase_project_id: str | None = None
    gptzero_api_key: str | None = None

    job_expiry_hours: int = 24  # NFR-PRIV-001

    @field_validator("database_url", mode="before")
    @classmethod
    def use_asyncpg_driver(cls, value: object) -> object:
        """Accept Railway's standard PostgreSQL URL without manual rewriting."""
        if isinstance(value, str) and value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
        return value

    def cors_origins(self) -> list[str]:
        """Return validated, non-empty CORS origins from deployment settings."""
        origins = [origin.strip().rstrip("/") for origin in self.allowed_origins.split(",") if origin.strip()]
        return origins or ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
