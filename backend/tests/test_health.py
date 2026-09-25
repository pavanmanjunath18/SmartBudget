from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import create_app


def test_health_ok_when_database_reachable(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_returns_503_when_database_unreachable() -> None:
    # Port 1 on localhost refuses connections immediately, so this is a real
    # connection failure rather than a mocked one.
    dead_engine = create_engine("postgresql+psycopg://nobody:nopass@localhost:1/none")

    def dead_db() -> Iterator[Session]:
        with Session(dead_engine) as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = dead_db
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
