"""Application settings, loaded from environment variables (and an optional .env file)."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Field names map to upper-case env vars, e.g. DATABASE_URL."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "SmartBudget"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://smartbudget:smartbudget@localhost:5434/smartbudget"
    test_database_url: str = (
        "postgresql+psycopg://smartbudget:smartbudget@localhost:5435/smartbudget_test"
    )
    # No default on purpose: the app refuses to start without a signing secret. The minimum
    # length also rejects a variable that is set but empty.
    jwt_secret_key: str = Field(min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    # Local folder for uploaded CSV files (replaced by S3 when deployed).
    upload_dir: str = "uploads"
    # Category suggestions for transactions no rule or cache entry matches.
    # "none" turns the LLM off; "anthropic" uses the Anthropic API (reads ANTHROPIC_API_KEY).
    llm_provider: str = "none"
    llm_model: str = "claude-opus-5"
    # Serverless (Vercel): each function instance is short-lived, so open a connection per
    # request instead of keeping a pool that would be frozen between invocations.
    db_use_null_pool: bool = False
    # Lets the shared demo account (demo@smartbudget.dev) wipe and rebuild its own data from
    # the UI. Off by default, so a normal install can't expose it.
    demo_reset_enabled: bool = False

    @field_validator("database_url", "test_database_url")
    @classmethod
    def use_psycopg_driver(cls, url: str) -> str:
        """Hosted Postgres (e.g. Neon) hands out postgres:// URLs; SQLAlchemy needs the driver."""
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url


@lru_cache
def get_settings() -> Settings:
    """Return the settings object, built once per process."""
    return Settings()
