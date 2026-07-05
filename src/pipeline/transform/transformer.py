"""Transformer: raw staged records -> clean StudyRecords.

This module connects the pieces:
  1. reads raw payloads from staging.raw_studies
  2. detects which format each payload is (API json or flat CSV row)
  3. sends it to the right parser
  4. yields the clean record together with its data quality issues

The database WRITE of clean records happens in the load step, not here.
Keeping transform and load separate makes both easier to test.
"""

from __future__ import annotations

import json
import logging
from typing import Iterator

from sqlalchemy import text

from pipeline.db import get_engine
from pipeline.transform.api_parser import parse_api_record
from pipeline.transform.csv_parser import parse_csv_record
from pipeline.transform.models import DQIssue, StudyRecord

logger = logging.getLogger(__name__)

# How many staged rows the database sends me per batch while streaming.
STREAM_BATCH_SIZE = 500


def detect_format(payload: dict) -> str:
    """'api' if the payload has the nested API structure, else 'csv'.

    (Records from the SQL source are flat rows too, so they take the
    csv path — the column aliases in the csv parser cover them.)
    """
    return "api" if "protocolSection" in payload else "csv"


def transform_payload(payload: dict) -> tuple[StudyRecord | None, list[DQIssue]]:
    """Clean one raw payload, whatever its format."""
    if detect_format(payload) == "api":
        return parse_api_record(payload)
    return parse_csv_record(payload)


def iter_staged_payloads(run_id: int | None = None) -> Iterator[dict]:
    """Stream raw payloads from staging, oldest first.

    With run_id I read one specific ingestion run.
    Without it I read everything in staging.
    """
    query = "SELECT payload FROM staging.raw_studies"
    params: dict = {}
    if run_id is not None:
        query += " WHERE run_id = :run_id"
        params["run_id"] = run_id
    query += " ORDER BY id"

    # I keep this connection open while streaming, and it only reads,
    # so I use a plain connection instead of a transaction.
    with get_engine().connect() as conn:
        result = conn.execution_options(yield_per=STREAM_BATCH_SIZE).execute(
            text(query), params
        )
        for row in result:
            payload = row[0]
            # psycopg2 usually gives jsonb back as a dict already,
            # but I handle the string case too, to be safe.
            if isinstance(payload, str):
                payload = json.loads(payload)
            yield payload


def transform_staged(
    run_id: int | None = None,
) -> Iterator[tuple[StudyRecord | None, list[DQIssue]]]:
    """The full transform stream: staged payload in, clean record out."""
    for payload in iter_staged_payloads(run_id):
        yield transform_payload(payload)
