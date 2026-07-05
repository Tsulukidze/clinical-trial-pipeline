"""SQL database source.

The challenge asks for three source types: CSV files, JSON APIs and
SQL databases. This module covers the third one: it connects to any
external SQL database (by connection URL) and streams rows from a
table or a custom query.

A realistic example: a client gives us read access to their own
trials database, and we pull the data from there into our pipeline.
"""

from __future__ import annotations

import logging
from typing import Iterator

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

# How many rows I pull from the source database per network round trip.
FETCH_BATCH_SIZE = 1_000


def read_sql_records(connection_url: str, query: str) -> Iterator[dict]:
    """Yield each row of the query result as a dict.

    Arguments:
        connection_url: SQLAlchemy URL of the SOURCE database,
                        e.g. "postgresql+psycopg2://user:pass@host:5432/db"
                        or   "sqlite:///some_file.db"
        query:          the SELECT to run, e.g. "SELECT * FROM trials"

    I stream the result instead of loading it all at once, so this
    also works for large source tables.
    """
    engine = create_engine(connection_url)
    logger.info("Reading from external SQL database")

    total = 0
    with engine.connect() as conn:
        # yield_per makes the driver fetch rows in batches, not all at once.
        result = conn.execution_options(yield_per=FETCH_BATCH_SIZE).execute(text(query))
        for row in result.mappings():
            total += 1
            yield dict(row)

    logger.info("Finished reading from SQL source, %d rows total", total)
