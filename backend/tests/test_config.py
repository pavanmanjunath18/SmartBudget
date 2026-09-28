import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db-host:5432/prod")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("JWT_SECRET_KEY", "from-env-0123456789")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://u:p@db-host:5432/prod"
    assert settings.log_level == "WARNING"
    assert settings.jwt_secret_key == "from-env-0123456789"


def test_missing_jwt_secret_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError, match="jwt_secret_key"):
        Settings(_env_file=None)


def test_hosted_postgres_urls_get_the_psycopg_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-0123456789")
    for url in ("postgres://u:p@host/db?sslmode=require", "postgresql://u:p@host/db"):
        monkeypatch.setenv("DATABASE_URL", url)

        settings = Settings(_env_file=None)

        assert settings.database_url.startswith("postgresql+psycopg://u:p@host/db")


@pytest.mark.parametrize("secret", ["", "too-short"])
def test_empty_or_short_jwt_secret_is_rejected(
    monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", secret)

    with pytest.raises(ValidationError, match="jwt_secret_key"):
        Settings(_env_file=None)
