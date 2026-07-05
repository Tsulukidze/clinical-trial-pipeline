"""Ingestion run tracking.

Every pipeline execution gets a row in clinical.ingestion_runs.
I create the row when the run starts and update it when the run
ends (success or failure). This way I can always answer: what ran,
when, from which source, how many records, and did it work.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from pipeline.db import transaction

logger = logging.getLogger(__name__)


def start_run(source_name: str, source_type: str) -> int:
    """Insert a new run row and return its id."""
    with transaction() as conn:
        run_id = conn.execute(
            text(
                """
                INSERT INTO clinical.ingestion_runs (source_name, source_type)
                VALUES (:source_name, :source_type)
                RETURNING run_id
                """
            ),
            {"source_name": source_name, "source_type": source_type},
        ).scalar_one()
    logger.info("Started run %d (source=%s, type=%s)", run_id, source_name, source_type)
    return run_id


def finish_run(
    run_id: int,
    status: str,
    records_extracted: int = 0,
    records_loaded: int = 0,
    records_rejected: int = 0,
    error_message: str | None = None,
) -> None:
    """Close a run: set its final status and counters."""
    with transaction() as conn:
        conn.execute(
            text(
                """
                UPDATE clinical.ingestion_runs
                SET status            = :status,
                    completed_at      = now(),
                    records_extracted = :records_extracted,
                    records_loaded    = :records_loaded,
                    records_rejected  = :records_rejected,
                    error_message     = :error_message
                WHERE run_id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "status": status,
                "records_extracted": records_extracted,
                "records_loaded": records_loaded,
                "records_rejected": records_rejected,
                "error_message": error_message,
            },
        )
    logger.info("Finished run %d with status '%s'", run_id, status)
