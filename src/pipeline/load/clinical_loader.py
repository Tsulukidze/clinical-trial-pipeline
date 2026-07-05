"""Clinical loader: clean StudyRecords -> normalized tables.

This is the last stage of the pipeline. It takes the clean records
from the transform step and writes them into the clinical schema.

Two important behaviors:

1. Loading is REPEATABLE. Running the same data twice does not create
   duplicates. For the studies table I use Postgres "upsert"
   (INSERT ... ON CONFLICT ... DO UPDATE). For child rows
   (interventions, locations, ...) I delete the old rows of that study
   and insert the fresh ones.

2. Data quality issues found by the transform step are saved into
   clinical.data_quality_issues, so they can be queried later, not
   just printed once.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.db import transaction
from pipeline.transform.models import DQIssue, StudyRecord
from pipeline.transform.transformer import transform_staged

logger = logging.getLogger(__name__)

# How many studies I load per database transaction.
BATCH_SIZE = 100


# ----------------------------------------------------------------------------
# small pieces
# ----------------------------------------------------------------------------

def _get_or_create_id(
    conn: Connection,
    cache: dict[str, int],
    sql: str,
    name: str,
    params: dict | None = None,
) -> int:
    """Return the id of a dimension row (condition, sponsor, country).

    I first look in a small in-memory cache, because the same values
    repeat constantly (thousands of studies share 'COVID-19'). Only on
    a cache miss do I touch the database.

    The SQL is an upsert that RETURNS the id in both cases: freshly
    inserted or already existing.
    """
    if name in cache:
        return cache[name]
    row_id = conn.execute(text(sql), params or {"name": name}).scalar_one()
    cache[name] = row_id
    return row_id


_UPSERT_STUDY = text(
    """
    INSERT INTO clinical.studies (
        nct_id, brief_title, official_title, study_type, overall_status,
        phase, enrollment, enrollment_type, start_date,
        primary_completion_date, completion_date, why_stopped,
        has_results, source
    ) VALUES (
        :nct_id, :brief_title, :official_title, :study_type, :overall_status,
        :phase, :enrollment, :enrollment_type, :start_date,
        :primary_completion_date, :completion_date, :why_stopped,
        :has_results, :source
    )
    ON CONFLICT (nct_id) DO UPDATE SET
        brief_title             = EXCLUDED.brief_title,
        official_title          = EXCLUDED.official_title,
        study_type              = EXCLUDED.study_type,
        overall_status          = EXCLUDED.overall_status,
        phase                   = EXCLUDED.phase,
        enrollment              = EXCLUDED.enrollment,
        enrollment_type         = EXCLUDED.enrollment_type,
        start_date              = EXCLUDED.start_date,
        primary_completion_date = EXCLUDED.primary_completion_date,
        completion_date         = EXCLUDED.completion_date,
        why_stopped             = EXCLUDED.why_stopped,
        has_results             = EXCLUDED.has_results,
        source                  = EXCLUDED.source,
        last_updated_at         = now()
    """
)

# For dimension tables the DO UPDATE trick makes Postgres return the id
# in both cases (new row or existing row).
_UPSERT_CONDITION = """
    INSERT INTO clinical.conditions (name) VALUES (:name)
    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
    RETURNING condition_id
"""

_UPSERT_SPONSOR = """
    INSERT INTO clinical.sponsors (name, agency_class) VALUES (:name, :agency_class)
    ON CONFLICT (name) DO UPDATE SET
        agency_class = COALESCE(clinical.sponsors.agency_class, EXCLUDED.agency_class)
    RETURNING sponsor_id
"""

_UPSERT_COUNTRY = """
    INSERT INTO clinical.countries (name) VALUES (:name)
    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
    RETURNING country_id
"""

# Child tables I clear and refill on every load of a study.
_CHILD_TABLES = (
    "clinical.study_conditions",
    "clinical.study_sponsors",
    "clinical.interventions",
    "clinical.study_locations",
    "clinical.outcomes",
    "clinical.eligibility",
)


# ----------------------------------------------------------------------------
# loading one study
# ----------------------------------------------------------------------------

def load_study(conn: Connection, record: StudyRecord, caches: dict[str, dict]) -> None:
    """Write one clean study and all its child rows."""
    conn.execute(
        _UPSERT_STUDY,
        {
            "nct_id": record.nct_id,
            "brief_title": record.brief_title,
            "official_title": record.official_title,
            "study_type": record.study_type,
            "overall_status": record.overall_status,
            "phase": record.phase,
            "enrollment": record.enrollment,
            "enrollment_type": record.enrollment_type,
            "start_date": record.start_date,
            "primary_completion_date": record.primary_completion_date,
            "completion_date": record.completion_date,
            "why_stopped": record.why_stopped,
            "has_results": record.has_results,
            "source": "pipeline",
        },
    )

    # Reloading a study: out with its old child rows, in with the new.
    for table in _CHILD_TABLES:
        conn.execute(
            text(f"DELETE FROM {table} WHERE nct_id = :nct_id"),  # noqa: S608 - table names are my own constants
            {"nct_id": record.nct_id},
        )

    # conditions ------------------------------------------------------------
    for name in record.conditions:
        condition_id = _get_or_create_id(conn, caches["conditions"], _UPSERT_CONDITION, name)
        conn.execute(
            text(
                "INSERT INTO clinical.study_conditions (nct_id, condition_id) "
                "VALUES (:nct_id, :condition_id) ON CONFLICT DO NOTHING"
            ),
            {"nct_id": record.nct_id, "condition_id": condition_id},
        )

    # sponsors -----------------------------------------------------------------
    for sponsor in record.sponsors:
        sponsor_id = _get_or_create_id(
            conn, caches["sponsors"], _UPSERT_SPONSOR, sponsor.name,
            {"name": sponsor.name, "agency_class": sponsor.agency_class},
        )
        conn.execute(
            text(
                "INSERT INTO clinical.study_sponsors (nct_id, sponsor_id, role) "
                "VALUES (:nct_id, :sponsor_id, :role) ON CONFLICT DO NOTHING"
            ),
            {"nct_id": record.nct_id, "sponsor_id": sponsor_id, "role": sponsor.role},
        )

    # interventions -----------------------------------------------------------
    if record.interventions:
        conn.execute(
            text(
                "INSERT INTO clinical.interventions (nct_id, intervention_type, name, description) "
                "VALUES (:nct_id, :intervention_type, :name, :description)"
            ),
            [
                {
                    "nct_id": record.nct_id,
                    "intervention_type": item.intervention_type,
                    "name": item.name,
                    "description": item.description,
                }
                for item in record.interventions
            ],
        )

    # locations ------------------------------------------------------------------
    for location in record.locations:
        country_id = None
        if location.country:
            country_id = _get_or_create_id(
                conn, caches["countries"], _UPSERT_COUNTRY, location.country
            )
        conn.execute(
            text(
                "INSERT INTO clinical.study_locations (nct_id, facility, city, state, country_id) "
                "VALUES (:nct_id, :facility, :city, :state, :country_id)"
            ),
            {
                "nct_id": record.nct_id,
                "facility": location.facility,
                "city": location.city,
                "state": location.state,
                "country_id": country_id,
            },
        )

    # outcomes ---------------------------------------------------------------
    if record.outcomes:
        conn.execute(
            text(
                "INSERT INTO clinical.outcomes (nct_id, outcome_type, measure, time_frame) "
                "VALUES (:nct_id, :outcome_type, :measure, :time_frame)"
            ),
            [
                {
                    "nct_id": record.nct_id,
                    "outcome_type": item.outcome_type,
                    "measure": item.measure,
                    "time_frame": item.time_frame,
                }
                for item in record.outcomes
            ],
        )

    # eligibility ---------------------------------------------------------------
    if record.eligibility:
        conn.execute(
            text(
                "INSERT INTO clinical.eligibility "
                "(nct_id, sex, min_age_years, max_age_years, healthy_volunteers) "
                "VALUES (:nct_id, :sex, :min_age, :max_age, :healthy)"
            ),
            {
                "nct_id": record.nct_id,
                "sex": record.eligibility.sex,
                "min_age": record.eligibility.min_age_years,
                "max_age": record.eligibility.max_age_years,
                "healthy": record.eligibility.healthy_volunteers,
            },
        )


def save_issues(conn: Connection, issues: list[DQIssue], run_id: int | None) -> None:
    """Persist data quality issues so they can be queried later."""
    if not issues:
        return
    conn.execute(
        text(
            "INSERT INTO clinical.data_quality_issues "
            "(run_id, nct_id, field_name, issue_type, raw_value, action) "
            "VALUES (:run_id, :nct_id, :field_name, :issue_type, :raw_value, :action)"
        ),
        [
            {
                "run_id": run_id,
                "nct_id": issue.nct_id,
                "field_name": issue.field_name,
                "issue_type": issue.issue_type,
                "raw_value": issue.raw_value,
                "action": issue.action,
            }
            for issue in issues
        ],
    )


# ----------------------------------------------------------------------------
# the full process step
# ----------------------------------------------------------------------------

def process_staged(run_id: int | None = None) -> dict[str, int]:
    """Transform everything in staging (or one run) and load it.

    Works in batches: BATCH_SIZE studies per transaction. If something
    fails, only the current batch rolls back, and the error goes up.

    Returns counters for the CLI report.
    """
    stats = {"loaded": 0, "rejected": 0, "issues": 0}
    # One shared cache per process call, so repeated dimension values
    # (the same condition, sponsor, country) cost one query in total.
    caches: dict[str, dict] = {"conditions": {}, "sponsors": {}, "countries": {}}

    batch_records: list[StudyRecord] = []
    batch_issues: list[DQIssue] = []

    def flush() -> None:
        """Write the current batch in one transaction."""
        if not batch_records and not batch_issues:
            return
        with transaction() as conn:
            for record in batch_records:
                load_study(conn, record, caches)
            save_issues(conn, batch_issues, run_id)
        stats["loaded"] += len(batch_records)
        stats["issues"] += len(batch_issues)
        logger.info("Loaded %d studies so far", stats["loaded"])
        batch_records.clear()
        batch_issues.clear()

    for record, issues in transform_staged(run_id):
        if record is None:
            stats["rejected"] += 1
        else:
            batch_records.append(record)
        batch_issues.extend(issues)
        if len(batch_records) >= BATCH_SIZE:
            flush()
    flush()  # whatever is left after the loop

    # If I processed one specific ingestion run, I also store how many
    # of its records were rejected, so the run row tells the full story.
    if run_id is not None:
        with transaction() as conn:
            conn.execute(
                text(
                    "UPDATE clinical.ingestion_runs "
                    "SET records_rejected = :rejected WHERE run_id = :run_id"
                ),
                {"rejected": stats["rejected"], "run_id": run_id},
            )

    return stats
