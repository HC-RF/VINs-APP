"""Engine and session management.

PostgreSQL when ``DATABASE_URL`` is set, SQLite otherwise. The fallback keeps
the app runnable with no infrastructure; everything above this module is
unaware of which one is in use.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import Base

log = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _build_engine(settings: Settings) -> Engine:
    url = settings.resolved_database_url
    kwargs: dict = {"echo": settings.db_echo, "future": True}

    if url.startswith("sqlite"):
        # FastAPI serves requests from a thread pool; SQLite needs telling.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs.update(
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,       # survive a database restart
            pool_recycle=1800,
        )

    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")      # concurrent readers
            cursor.execute("PRAGMA foreign_keys=ON")       # honour ON DELETE CASCADE
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine(settings or get_settings())
    return _engine


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(settings), autoflush=False, expire_on_commit=False, future=True
        )
    return _SessionFactory


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on failure."""
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session


def init_db(settings: Settings | None = None) -> None:
    """Create tables if absent and seed the provider registry rows."""
    settings = settings or get_settings()
    engine = get_engine(settings)
    Base.metadata.create_all(engine)
    log.info(
        "Database ready (%s)",
        "SQLite fallback" if settings.using_sqlite else "PostgreSQL",
    )
    _seed_data_sources(settings)


def _seed_data_sources(settings: Settings) -> None:
    """Mirror the provider registry into ``data_sources``."""
    from app.db.models import DataSource
    from app.providers.registry import get_registry

    registry = get_registry()
    with session_scope(settings) as session:
        existing = {row.name: row for row in session.query(DataSource).all()}
        for provider in registry.all:
            info = provider.info()
            row = existing.get(info.name)
            if row is None:
                session.add(
                    DataSource(
                        name=info.name, label=info.label, kind=info.kind.value,
                        priority=info.priority, cost_per_call=info.cost_per_call,
                        enabled=info.enabled, description=info.description,
                    )
                )
            else:
                row.label = info.label
                row.kind = info.kind.value
                row.priority = info.priority
                row.cost_per_call = info.cost_per_call
                row.enabled = info.enabled
                row.description = info.description


def reset_engine() -> None:
    """Drop cached engine/session state. Used by tests."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
