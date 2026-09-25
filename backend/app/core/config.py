"""Application settings, loaded from environment variables (and an optional .env file)."""

from functools import lru_cache

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
    # No default on purpose: the app refuses to start without a signing secret.
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60


@lru_cache
def get_settings() -> Settings:
    """Return the settings object, built once per process."""
    return Settings()
