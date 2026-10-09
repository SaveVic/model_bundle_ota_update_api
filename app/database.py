from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()

_connect_args: dict[str, object] = {}
if settings.database_url.startswith("sqlite"):
    # FastAPI serves requests from a threadpool; SQLite needs this to be shared.
    _connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    # Import models so they are registered on Base.metadata before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _lightweight_migrations()


def _lightweight_migrations() -> None:
    """Additive columns this demo added after a DB may already exist. SQLite only,
    best-effort — a real deployment would use Alembic."""
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "bundle_versions" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("bundle_versions")}
    if "changelog" not in cols:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE bundle_versions ADD COLUMN changelog TEXT DEFAULT '[]'")
            )
