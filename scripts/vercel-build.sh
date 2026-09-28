#!/usr/bin/env sh
# Vercel build step. Sensitive environment variables (like DATABASE_URL) are only readable
# inside Vercel, so database migrations run here, as a release step, before the new
# version goes live.
set -e

if [ "$VERCEL_ENV" = "production" ]; then
  # The build image's system Python is externally managed (PEP 668), so use a throwaway
  # virtualenv outside the project for the migration tooling.
  python3 -m venv /tmp/migrate-env
  PY=/tmp/migrate-env/bin/python
  $PY -m pip install --quiet -r requirements.txt
  # Migrations use the direct (unpooled) connection; schema changes shouldn't go
  # through the connection pooler. Only production deploys migrate, so a preview
  # deploy of a pull request can't change the live database.
  (cd backend && DATABASE_URL="$DATABASE_URL_UNPOOLED" $PY -m alembic upgrade head)
  if [ "$SEED_DEMO_DATA" = "true" ]; then
    # Idempotent: skips if the demo user already exists.
    (cd backend && DATABASE_URL="$DATABASE_URL_UNPOOLED" $PY -m app.scripts.seed)
  fi
fi

npm --prefix frontend run build
