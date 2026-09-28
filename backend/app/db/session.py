"""Database engine and per-request session."""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


def build_engine() -> Engine:
    """A pooled engine for normal servers; a pool-less one for serverless functions.

    Serverless connections go through the database's own connection pooler (PgBouncer on
    Neon), which doesn't keep server-side prepared statements between transactions, so
    psycopg's automatic statement preparation is turned off there.
    """
    settings = get_settings()
    if settings.db_use_null_pool:
        return create_engine(
            settings.database_url, poolclass=NullPool, connect_args={"prepare_threshold": None}
        )
    return create_engine(settings.database_url, pool_pre_ping=True)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yield one session per request and always close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
