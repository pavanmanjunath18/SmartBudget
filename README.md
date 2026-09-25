# SmartBudget

Double-entry bookkeeping for freelancers and small businesses. Work in progress; see
[docs/decisions.md](docs/decisions.md) for the design log.

## Quick start (backend)

Requirements: Python 3.11+, Docker.

```bash
docker compose up -d --wait db                       # dev Postgres on port 5434
docker compose --profile test up -d --wait db_test   # test Postgres on port 5435
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # then set JWT_SECRET_KEY
alembic upgrade head
uvicorn app.main:app --reload        # http://localhost:8000/api/v1/health
```

## Checks

```bash
cd backend
ruff check . && ruff format --check .
pytest
```
