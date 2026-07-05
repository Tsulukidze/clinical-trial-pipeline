"""Analytics queries.

Each query below answers one of the questions from the challenge.
I keep them as plain SQL on purpose: the analytical logic lives in
the database, where set-based work belongs, and the SQL itself stays
visible and reviewable.

Postgres features I use and why:
  * FILTER (WHERE ...)        - count a subset inside one aggregate pass
  * window over aggregate     - percentage of total without a second query
  * percentile_cont           - a true median (avg alone hides outliers)
  * CTE (WITH ...)            - name an intermediate step, keep it readable
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from pipeline.db import get_engine


@dataclass(frozen=True)
class Report:
    key: str            # name used on the command line
    title: str          # human heading printed above the table
    sql: str
    uses_top: bool      # True if the query takes a :top row limit


REPORTS: list[Report] = [
    # ------------------------------------------------------------------
    # Question 1: how many trials by study type and phase?
    # ------------------------------------------------------------------
    Report(
        key="type_phase",
        title="Trials by study type and phase",
        uses_top=False,
        sql="""
            SELECT
                COALESCE(study_type, 'UNKNOWN') AS study_type,
                COALESCE(phase, 'UNKNOWN')      AS phase,
                count(*)                        AS trials,
                -- share of all trials, computed with a window over the
                -- aggregate so I do not need a second query for the total
                round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
            FROM clinical.studies
            GROUP BY study_type, phase
            ORDER BY trials DESC
        """,
    ),
    # ------------------------------------------------------------------
    # Question 2: what are the most common conditions being studied?
    # ------------------------------------------------------------------
    Report(
        key="conditions",
        title="Most common conditions",
        uses_top=True,
        sql="""
            SELECT
                c.name                    AS condition,
                count(sc.nct_id)          AS studies,
                round(100.0 * count(sc.nct_id)
                      / (SELECT count(*) FROM clinical.studies), 1) AS pct_of_studies
            FROM clinical.conditions c
            JOIN clinical.study_conditions sc ON sc.condition_id = c.condition_id
            GROUP BY c.name
            ORDER BY studies DESC, condition
            LIMIT :top
        """,
    ),
    # ------------------------------------------------------------------
    # Question 3: which interventions have the highest completion rates?
    # ------------------------------------------------------------------
    Report(
        key="completion",
        title="Completion rate by intervention type",
        uses_top=False,
        sql="""
            -- Step 1 (CTE): per intervention type, count its studies and
            -- how many of them finished. FILTER counts the completed
            -- subset in the same pass as the total.
            WITH per_type AS (
                SELECT
                    COALESCE(i.intervention_type, 'UNKNOWN') AS intervention_type,
                    count(DISTINCT s.nct_id)                 AS studies,
                    count(DISTINCT s.nct_id)
                        FILTER (WHERE s.overall_status = 'COMPLETED') AS completed,
                    count(DISTINCT s.nct_id)
                        FILTER (WHERE s.overall_status IN ('TERMINATED', 'WITHDRAWN', 'SUSPENDED'))
                                                             AS stopped_early
                FROM clinical.interventions i
                JOIN clinical.studies s ON s.nct_id = i.nct_id
                GROUP BY i.intervention_type
            )
            -- Step 2: turn counts into rates. I require at least 5
            -- studies, because a rate computed from 1-2 studies is noise.
            SELECT
                intervention_type,
                studies,
                completed,
                stopped_early,
                round(100.0 * completed / studies, 1) AS completion_rate_pct
            FROM per_type
            WHERE studies >= 5
            ORDER BY completion_rate_pct DESC
        """,
    ),
    # ------------------------------------------------------------------
    # Question 4: geographic distribution of clinical trials
    # ------------------------------------------------------------------
    Report(
        key="geography",
        title="Geographic distribution (by country)",
        uses_top=True,
        sql="""
            SELECT
                co.name                    AS country,
                count(DISTINCT sl.nct_id)  AS studies,
                count(*)                   AS sites,
                -- a multi-site country hosts the same study in many
                -- places; this ratio shows how spread out trials are
                round(count(*)::numeric / count(DISTINCT sl.nct_id), 1) AS sites_per_study
            FROM clinical.study_locations sl
            JOIN clinical.countries co ON co.country_id = sl.country_id
            GROUP BY co.name
            ORDER BY studies DESC, country
            LIMIT :top
        """,
    ),
    # ------------------------------------------------------------------
    # Question 5: timeline analysis of study durations
    # ------------------------------------------------------------------
    Report(
        key="timeline",
        title="Study durations by phase",
        uses_top=False,
        sql="""
            SELECT
                COALESCE(phase, 'UNKNOWN') AS phase,
                count(*)                   AS trials,
                count(duration_days)       AS with_known_duration,
                round(avg(duration_days))                    AS avg_days,
                -- the median resists outliers (one 20-year study would
                -- drag the average up but barely moves the median)
                round(percentile_cont(0.5)
                      WITHIN GROUP (ORDER BY duration_days)) AS median_days,
                min(start_date)            AS earliest_start,
                max(start_date)            AS latest_start
            FROM clinical.studies
            GROUP BY phase
            ORDER BY trials DESC
        """,
    ),
    # ------------------------------------------------------------------
    # Extra: patient enrollment overview (functional requirement)
    # ------------------------------------------------------------------
    Report(
        key="enrollment",
        title="Patient enrollment by study status",
        uses_top=False,
        sql="""
            SELECT
                COALESCE(overall_status, 'UNKNOWN') AS status,
                count(*)                            AS trials,
                sum(enrollment)                     AS total_participants,
                round(avg(enrollment))              AS avg_per_trial,
                max(enrollment)                     AS largest_trial
            FROM clinical.studies
            GROUP BY overall_status
            ORDER BY trials DESC
        """,
    ),
]


def run_report(report: Report, top: int = 10) -> tuple[list[str], list[tuple]]:
    """Execute one report. Returns (column names, rows)."""
    params = {"top": top} if report.uses_top else {}
    with get_engine().connect() as conn:
        result = conn.execute(text(report.sql), params)
        return list(result.keys()), [tuple(row) for row in result]


def get_report(key: str) -> Report | None:
    """Find a report by its command line name."""
    for report in REPORTS:
        if report.key == key:
            return report
    return None
