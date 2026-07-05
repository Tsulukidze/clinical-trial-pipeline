"""Data models for the transform step.

These dataclasses describe what a CLEAN study looks like after
validation. The parsers (API and CSV) both produce this same shape,
so the loader in the next step only needs to understand one format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class DQIssue:
    """One data quality problem I found in one field of one record."""

    field_name: str
    issue_type: str        # for example 'invalid_date', 'negative_enrollment'
    raw_value: str | None  # the original bad value, kept for debugging
    action: str            # what I did about it: 'set_null', 'rejected_record', ...
    nct_id: str | None = None


@dataclass
class Sponsor:
    name: str
    agency_class: str | None
    role: str              # 'LEAD' or 'COLLABORATOR'


@dataclass
class Intervention:
    intervention_type: str | None
    name: str
    description: str | None = None


@dataclass
class Location:
    facility: str | None
    city: str | None
    state: str | None
    country: str | None


@dataclass
class Outcome:
    outcome_type: str      # 'PRIMARY', 'SECONDARY' or 'OTHER'
    measure: str
    time_frame: str | None


@dataclass
class Eligibility:
    sex: str | None
    min_age_years: float | None
    max_age_years: float | None
    healthy_volunteers: bool | None


@dataclass
class StudyRecord:
    """One clean study, ready to be loaded into the clinical schema."""

    nct_id: str
    brief_title: str
    official_title: str | None = None
    study_type: str | None = None
    overall_status: str | None = None
    phase: str | None = None
    enrollment: int | None = None
    enrollment_type: str | None = None
    start_date: date | None = None
    primary_completion_date: date | None = None
    completion_date: date | None = None
    why_stopped: str | None = None
    has_results: bool = False

    conditions: list[str] = field(default_factory=list)
    sponsors: list[Sponsor] = field(default_factory=list)
    interventions: list[Intervention] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    eligibility: Eligibility | None = None
