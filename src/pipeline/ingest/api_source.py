"""ClinicalTrials.gov API v2 source.

Downloads studies from https://clinicaltrials.gov/api/v2/studies
and yields one dict per study (the raw JSON from the API).

The API returns results in pages. Each response contains a
"nextPageToken" that I send back to get the next page, until
the token is missing (that means I reached the end).
"""

from __future__ import annotations

import logging
import time
from typing import Iterator

import requests

from pipeline.config import settings

logger = logging.getLogger(__name__)

# How many times I retry a failed request before giving up.
MAX_RETRIES = 3
# How long I wait before the first retry (doubles every retry).
RETRY_WAIT_SECONDS = 2.0


def _get_with_retries(session: requests.Session, url: str, params: dict) -> dict:
    """Send one GET request, retrying on temporary errors.

    I retry on network errors, on 429 (too many requests) and on
    5xx (server problems). I do NOT retry on other 4xx errors,
    because those mean my request itself is wrong and a retry
    would just fail again.
    """
    wait = RETRY_WAIT_SECONDS
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(
                url, params=params, timeout=settings.ctgov_timeout_seconds
            )
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(
                    f"Temporary error, status {response.status_code}"
                )
            response.raise_for_status()
            return response.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                break
            logger.warning(
                "Request failed (attempt %d/%d): %s. Retrying in %.0fs",
                attempt, MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
            wait *= 2  # wait longer each time, to give the server a break

    raise RuntimeError(f"API request failed after {MAX_RETRIES} attempts") from last_error


def fetch_api_records(
    max_records: int = 1_000,
    query_condition: str | None = None,
) -> Iterator[dict]:
    """Yield studies from the ClinicalTrials.gov API, one dict each.

    Arguments:
        max_records:     stop after this many studies. A safety limit,
                         because the full registry has 500k+ studies.
        query_condition: optional filter, e.g. "covid-19", to download
                         only studies about one condition.
    """
    session = requests.Session()
    params: dict = {"pageSize": settings.ctgov_page_size}
    if query_condition:
        params["query.cond"] = query_condition

    fetched = 0
    page_number = 0

    while fetched < max_records:
        page_number += 1
        data = _get_with_retries(session, settings.ctgov_base_url, params)
        studies = data.get("studies", [])

        if not studies:
            break  # empty page means there is nothing more to read

        for study in studies:
            yield study
            fetched += 1
            if fetched >= max_records:
                break

        logger.info("Fetched page %d (%d studies so far)", page_number, fetched)

        next_token = data.get("nextPageToken")
        if not next_token:
            break  # no token = last page
        params["pageToken"] = next_token

    logger.info("API download finished, %d studies total", fetched)


def extract_nct_id_from_api(study: dict) -> str | None:
    """Pull the NCT ID out of the nested API JSON.

    The API puts it under protocolSection -> identificationModule -> nctId.
    I return None if any level is missing, instead of crashing.
    """
    return (
        study.get("protocolSection", {})
        .get("identificationModule", {})
        .get("nctId")
    )
