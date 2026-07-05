"""Staging loader.

Takes raw records from any source (CSV, API, SQL) and saves them
into staging.raw_studies as JSON, exactly as they arrived.

Why I keep a raw copy:
  * if I find a bug in the transform step later, I can fix it and
    re-run the transform from staging, without downloading again
  * I can always look at the original value of any field when
    debugging a data quality problem
"""

from __future__ import annotations

import json
import logging
from typing import Iterable, Iterator

from sqlalchemy import text

from pipeline.db import transaction

logger = logging.getLogger(__name__)

# How many records I insert per database transaction.
# One-by-one inserts would be very slow; one giant insert would use
# too much memory. Batches are the middle ground.
BATCH_SIZE = 1_000

# Column names where the NCT ID can appear in flat records (CSV / SQL).
# Different exports name this column differently.
_NCT_ID_KEYS = ("nct_id", "nct_number", "nctid")


def _extract_nct_id(record: dict) -> str | None:
    """Try to find the NCT ID inside a record.

    I check the flat column names first (CSV and SQL sources),
    then the nested path used by the ClinicalTrials.gov API.
    Returns None if I cannot find it — the record still lands in
    staging, and the transform step will reject it there with a
    logged data quality issue.
    """
    for key in _NCT_ID_KEYS:
        value = record.get(key)
        if value:
            return str(value).strip()

    # Nested path from the API: protocolSection.identificationModule.nctId
    nested = (
        record.get("protocolSection", {})
        .get("identificationModule", {})
        .get("nctId")
    )
    if nested:
        return str(nested).strip()

    return None


def _batches(records: Iterable[dict], size: int) -> Iterator[list[dict]]:
    """Group a stream of records into lists of `size` items."""
    batch: list[dict] = []
    for record in records:
        batch.append(record)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def load_to_staging(run_id: int, source: str, records: Iterable[dict]) -> int:
    """Insert raw records into staging.raw_studies. Returns how many.

    Each batch is one transaction: if an insert in the middle of a
    batch fails, that whole batch rolls back and the error goes up
    to the caller, which then marks the run as failed.
    """
    insert_sql = text(
        """
        INSERT INTO staging.raw_studies (run_id, source, nct_id, payload)
        VALUES (:run_id, :source, :nct_id, CAST(:payload AS jsonb))
        """
    )

    total = 0
    for batch in _batches(records, BATCH_SIZE):
        rows = [
            {
                "run_id": run_id,
                "source": source,
                "nct_id": _extract_nct_id(record),
                # default=str handles values json does not know,
                # for example dates coming from a SQL source
                "payload": json.dumps(record, default=str),
            }
            for record in batch
        ]
        with transaction() as conn:
            conn.execute(insert_sql, rows)
        total += len(rows)
        logger.info("Staged %d records so far", total)

    return total
