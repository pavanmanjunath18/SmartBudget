from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.models import User

SIGNUP = "/api/v1/auth/signup"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"


def _signup(client: TestClient, email: str = "ana@example.com", password: str = "correct-horse"):
    return client.post(
        SIGNUP, json={"email": email, "password": password, "organization_name": "Ana Design"}
    )


def test_signup_creates_user_and_owned_org(client: TestClient) -> None:
    response = _signup(client, email="  Ana@Example.com ")

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ana@example.com"
    assert "password" not in body and "password_hash" not in body

    token = client.post(LOGIN, json={"email": "ana@example.com", "password": "correct-horse"})
    me = client.get(ME, headers={"Authorization": f"Bearer {token.json()['access_token']}"})
    assert me.status_code == 200
    orgs = me.json()["organizations"]
    assert [(o["name"], o["role"]) for o in orgs] == [("Ana Design", "owner")]


def test_password_is_stored_hashed(client: TestClient, db_session: Session) -> None:
    _signup(client)

    user = db_session.scalars(select(User).where(User.email == "ana@example.com")).one()
    assert user.password_hash != "correct-horse"
    assert user.password_hash.startswith("$argon2")


def test_duplicate_email_is_rejected_case_insensitively(client: TestClient) -> None:
    assert _signup(client, email="ana@example.com").status_code == 201

    response = _signup(client, email="ANA@example.com")

    assert response.status_code == 409


def test_short_password_is_rejected(client: TestClient) -> None:
    assert _signup(client, password="short").status_code == 422


def test_login_with_wrong_password_or_unknown_email_gives_same_error(client: TestClient) -> None:
    _signup(client)

    wrong_password = client.post(LOGIN, json={"email": "ana@example.com", "password": "nope-nope"})
    unknown_email = client.post(LOGIN, json={"email": "bob@example.com", "password": "nope-nope"})

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_me_requires_a_token(client: TestClient) -> None:
    assert client.get(ME).status_code == 401


def test_garbage_token_is_rejected(client: TestClient) -> None:
    response = client.get(ME, headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401


def test_expired_token_is_rejected(client: TestClient, db_session: Session) -> None:
    _signup(client)
    user = db_session.scalars(select(User).where(User.email == "ana@example.com")).one()
    old_token = create_access_token(user.id, now=datetime.now(UTC) - timedelta(days=1))

    response = client.get(ME, headers={"Authorization": f"Bearer {old_token}"})

    assert response.status_code == 401


def test_token_signed_with_another_secret_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    _signup(client)
    user = db_session.scalars(select(User).where(User.email == "ana@example.com")).one()
    forged = jwt.encode(
        {"sub": str(user.id), "exp": datetime.now(UTC) + timedelta(hours=1)},
        "attacker-secret-key-of-reasonable-length",
        algorithm="HS256",
    )

    response = client.get(ME, headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401
