"""Database access helpers.

I use SQLAlchemy Core (engine + raw SQL), not the ORM. For a data
pipeline most of the work is plain SQL anyway, and writing it out
keeps it visible instead of hiding it behind ORM classes.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from pipeline.config import settings

logger = logging.getLogger(__name__)

# One engine for the whole process. The engine keeps a pool of
# connections, so I do not open a new connection for every query.
_engine: Engine | None = None


def get_engine() -> Engine:
    """Create the engine on first use, then reuse it."""
    global _engine
    if _engine is None:
        # pool_pre_ping: check a connection is still alive before
        # using it, instead of failing with a dead one.
        _engine = create_engine(settings.database_url, pool_pre_ping=True)
    return _engine


@contextmanager
def transaction() -> Iterator[Connection]:
    """One atomic transaction.

    Everything inside the `with` block either fully commits, or fully
    rolls back if any error happens. No half-written data.
    """
    with get_engine().begin() as conn:
        yield conn


def apply_schema(ddl_dir: str | Path = "sql") -> None:
    """Run every .sql file in the folder, in name order (001_, 002_, ...).

    Safe to run more than once, because all my DDL statements use
    IF NOT EXISTS. Useful when Postgres was started some other way
    than docker-compose and skipped the automatic init step.
    """
    ddl_path = Path(ddl_dir)
    for sql_file in sorted(ddl_path.glob("*.sql")):
        logger.info("Applying DDL: %s", sql_file.name)
        with transaction() as conn:
            conn.execute(text(sql_file.read_text()))


def healthcheck() -> bool:
    """Return True if the database answers a simple query."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database healthcheck failed: %s", exc)
        return False
