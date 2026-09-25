import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db-host:5432/prod")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("JWT_SECRET_KEY", "from-env")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://u:p@db-host:5432/prod"
    assert settings.log_level == "WARNING"
    assert settings.jwt_secret_key == "from-env"


def test_missing_jwt_secret_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError, match="jwt_secret_key"):
        Settings(_env_file=None)
