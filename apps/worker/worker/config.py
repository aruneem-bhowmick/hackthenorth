from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# See apps/api/app/config.py for why this fallback path exists.
_REPO_ROOT_ENV = Path(__file__).resolve().parent.parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", _REPO_ROOT_ENV), extra="ignore")

    database_url: str = "postgresql+asyncpg://pincite:pincite@localhost:5432/pincite"
    redis_url: str = "redis://localhost:6379/0"
    uploads_dir: str = "./data/uploads"

    sentry_dsn_worker: str | None = None
    sentry_environment: str = "development"
    courtlistener_api_token: str | None = None
    openai_api_key: str | None = None

    @field_validator("database_url", mode="before")
    @classmethod
    def use_asyncpg_driver(cls, value: object) -> object:
        """Accept Railway's standard PostgreSQL URL without manual rewriting."""
        if isinstance(value, str) and value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
