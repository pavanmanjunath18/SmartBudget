"""Shared pytest fixtures.

Tests run against a real Postgres (the `db_test` compose service), never the dev
database. Migrations are applied once per test run, and every test runs inside a
transaction that is rolled back afterwards, so tests cannot leak data into each other.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.db.session import get_db
from app.main import create_app
from app.services.storage import get_storage

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Engine for the test database, with all migrations applied."""
    url = get_settings().test_database_url
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(alembic_cfg, "head")

    test_engine = create_engine(url)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back at the end of the test.

    Code under test may call session.commit(); with create_savepoint mode that
    only releases a SAVEPOINT, and the outer transaction still gets rolled back.
    """
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


class InMemoryStorage:
    """Test double for FileStorage: keeps uploaded files in a dict."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def save(self, key: str, content: bytes) -> None:
        self.files[key] = content


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def client(db_session: Session, storage: InMemoryStorage) -> Iterator[TestClient]:
    """HTTP client whose requests use the rolled-back test session."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_storage] = lambda: storage
    with TestClient(app) as test_client:
        yield test_client


@dataclass
class LoggedInUser:
    email: str
    org_id: int
    headers: dict[str, str]


@pytest.fixture
def make_user(client: TestClient) -> Callable[..., LoggedInUser]:
    """Factory: sign up a user (with their first org), log in, return auth headers."""

    def _make(
        email: str, org_name: str = "Test Org", password: str = "correct-horse"
    ) -> LoggedInUser:
        signup = client.post(
            "/api/v1/auth/signup",
            json={"email": email, "password": password, "organization_name": org_name},
        )
        assert signup.status_code == 201, signup.text
        login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        orgs = client.get("/api/v1/orgs", headers=headers).json()
        return LoggedInUser(email=email, org_id=orgs[0]["id"], headers=headers)

    return _make


def account_ids(client: TestClient, user: LoggedInUser) -> dict[str, int]:
    """Map account code -> id for the user's first organization."""
    response = client.get(f"/api/v1/orgs/{user.org_id}/accounts", headers=user.headers)
    return {a["code"]: a["id"] for a in response.json()}


def post_entry(
    client: TestClient,
    user: LoggedInUser,
    lines: list[tuple[int, int, int]],
    entry_date: str = "2026-01-15",
    memo: str = "",
):
    """Post an entry from (account_id, debit_cents, credit_cents) tuples."""
    return client.post(
        f"/api/v1/orgs/{user.org_id}/journal-entries",
        headers=user.headers,
        json={
            "entry_date": entry_date,
            "memo": memo,
            "lines": [{"account_id": a, "debit_cents": d, "credit_cents": c} for a, d, c in lines],
        },
    )
