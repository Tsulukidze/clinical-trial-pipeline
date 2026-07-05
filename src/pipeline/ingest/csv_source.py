"""CSV source.

Reads a clinical trials CSV file and yields one dict per row.
This module only extracts data. It does not talk to the database,
so it is easy to test on its own.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

import pandas as pd

logger = logging.getLogger(__name__)

# I read the file in chunks so a very large CSV does not fill up memory.
CHUNK_SIZE = 5_000


def _clean_column_name(name: str) -> str:
    """Turn a raw header like ' NCT Number ' into 'nct_number'.

    Different CSV exports use different header styles, so I normalize
    them all to lowercase snake_case. This gives the transform step
    one predictable naming style to work with.
    """
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def read_csv_records(path: str | Path) -> Iterator[dict]:
    """Yield each CSV row as a plain dict.

    Rules I apply here:
      * column names are normalized (see _clean_column_name)
      * pandas NaN values are replaced with None, so the rest of the
        pipeline only has to deal with one kind of "missing"
    """
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    logger.info("Reading CSV file: %s", csv_path)

    # dtype=str keeps everything as text at this stage. Parsing numbers
    # and dates is the job of the transform step, where I can also log
    # bad values as data quality issues instead of crashing here.
    reader = pd.read_csv(csv_path, dtype=str, chunksize=CHUNK_SIZE)

    total = 0
    for chunk in reader:
        chunk.columns = [_clean_column_name(c) for c in chunk.columns]
        # Replace NaN with None so the dicts contain real Python values.
        chunk = chunk.astype(object).where(chunk.notna(), None)
        for record in chunk.to_dict(orient="records"):
            total += 1
            yield record

    logger.info("Finished reading CSV, %d rows total", total)
