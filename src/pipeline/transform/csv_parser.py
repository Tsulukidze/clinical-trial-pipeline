"""Parser for flat CSV records (Kaggle ClinicalTrials.gov exports).

CSV exports pack lists into single text cells with '|' between values:
    Conditions:     "COVID-19|Pneumonia"
    Interventions:  "Drug: Remdesivir|Other: Placebo"
    Locations:      "Mayo Clinic, Rochester, Minnesota, United States|..."

This module unpacks those cells and produces the same clean
StudyRecord as the API parser, so the loader sees no difference.
"""

from __future__ import annotations

from pipeline.transform import cleaners
from pipeline.transform.models import (
    DQIssue,
    Eligibility,
    Intervention,
    Location,
    StudyRecord,
)

# The same field sometimes has different column names in different
# exports. I try each name in order and take the first that has a value.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "nct_id": ("nct_number", "nct_id", "nctid"),
    "brief_title": ("title", "brief_title", "study_title"),
    "official_title": ("official_title",),
    "study_type": ("study_type",),
    "overall_status": ("status", "overall_status", "recruitment"),
    "phase": ("phases", "phase"),
    "enrollment": ("enrollment",),
    "start_date": ("start_date",),
    "primary_completion_date": ("primary_completion_date",),
    "completion_date": ("completion_date",),
    "conditions": ("conditions", "condition"),
    "interventions": ("interventions", "intervention"),
    "locations": ("locations", "location"),
    "sponsors": ("sponsor_collaborators", "sponsor/collaborators", "sponsors"),
    "sex": ("gender", "sex"),
    "age": ("age",),
}


def _get(record: dict, field: str) -> object:
    """Read a field trying all its known column names."""
    for column in _COLUMN_ALIASES.get(field, ()):
        if record.get(column) is not None:
            return record[column]
    return None


def _split_list(raw: object) -> list[str]:
    """Split a '|' packed cell into clean parts, dropping empties."""
    text = cleaners.clean_text(raw)
    if text is None:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


def _parse_date_field(raw: object, field: str, nct_id: str, issues: list[DQIssue]):
    """Same behavior as in the API parser: log, never crash."""
    try:
        value = cleaners.parse_date(raw)
    except ValueError:
        issues.append(DQIssue(field, "invalid_date", str(raw), "set_null", nct_id))
        return None
    if value is not None and cleaners.is_partial_date(raw):
        issues.append(
            DQIssue(field, "partial_date", str(raw), "approximated_to_period_start", nct_id)
        )
    return value


def parse_csv_record(record: dict) -> tuple[StudyRecord | None, list[DQIssue]]:
    """Turn one flat CSV row into a clean StudyRecord.

    Returns (record, issues). Unusable rows return (None, issues).
    """
    issues: list[DQIssue] = []

    # --- identity checks -------------------------------------------------
    raw_nct = _get(record, "nct_id")
    try:
        nct_id = cleaners.validate_nct_id(raw_nct)
    except ValueError:
        issues.append(DQIssue("nct_id", "invalid_nct_id", str(raw_nct), "rejected_record"))
        return None, issues

    brief_title = cleaners.clean_text(_get(record, "brief_title"))
    if brief_title is None:
        issues.append(DQIssue("brief_title", "missing_value", None, "rejected_record", nct_id))
        return None, issues

    # --- enrollment -------------------------------------------------------
    try:
        enrollment = cleaners.parse_enrollment(_get(record, "enrollment"))
    except ValueError:
        issues.append(
            DQIssue("enrollment", "invalid_enrollment", str(_get(record, "enrollment")), "set_null", nct_id)
        )
        enrollment = None

    # --- dates ---------------------------------------------------------------
    start_date = _parse_date_field(_get(record, "start_date"), "start_date", nct_id, issues)
    primary_completion_date = _parse_date_field(
        _get(record, "primary_completion_date"), "primary_completion_date", nct_id, issues
    )
    completion_date = _parse_date_field(
        _get(record, "completion_date"), "completion_date", nct_id, issues
    )
    if start_date and completion_date and completion_date < start_date:
        issues.append(
            DQIssue("completion_date", "completion_before_start", str(completion_date), "set_null", nct_id)
        )
        completion_date = None

    record_out = StudyRecord(
        nct_id=nct_id,
        brief_title=brief_title,
        official_title=cleaners.clean_text(_get(record, "official_title")),
        study_type=cleaners.standardize_enum(_get(record, "study_type")),
        overall_status=cleaners.standardize_enum(_get(record, "overall_status")),
        phase=cleaners.standardize_phase(_get(record, "phase")),
        enrollment=enrollment,
        enrollment_type=None,  # CSV exports do not separate actual/estimated
        start_date=start_date,
        primary_completion_date=primary_completion_date,
        completion_date=completion_date,
    )

    # --- conditions --------------------------------------------------------
    for name in _split_list(_get(record, "conditions")):
        if name.title() not in record_out.conditions:
            record_out.conditions.append(name.title())

    # --- interventions: cells look like "Drug: Remdesivir" ---------------
    for item in _split_list(_get(record, "interventions")):
        if ":" in item:
            type_part, name_part = item.split(":", 1)
            itype = cleaners.standardize_enum(type_part)
            name = cleaners.clean_text(name_part)
        else:
            itype, name = None, cleaners.clean_text(item)
        if name:
            record_out.interventions.append(Intervention(itype, name))

    # --- locations: "Facility, City, State, Country" ------------------------
    # The comma format is ambiguous (facility names can contain commas),
    # so I only trust the edges: first part = facility, last = country,
    # second-to-last = state or city. This is a documented compromise.
    for item in _split_list(_get(record, "locations")):
        parts = [p.strip() for p in item.split(",")]
        if len(parts) >= 3:
            country = parts[-1].title()
            record_out.locations.append(
                Location(facility=parts[0], city=parts[-3] if len(parts) >= 4 else None,
                         state=parts[-2], country=country)
            )
        elif len(parts) == 2:
            record_out.locations.append(
                Location(facility=parts[0], city=None, state=None, country=parts[-1].title())
            )
        elif parts and parts[0]:
            record_out.locations.append(
                Location(facility=parts[0], city=None, state=None, country=None)
            )

    # --- sponsors: first one is the lead, the rest are collaborators -----
    from pipeline.transform.models import Sponsor  # local import to avoid clutter above

    for position, name in enumerate(_split_list(_get(record, "sponsors"))):
        role = "LEAD" if position == 0 else "COLLABORATOR"
        record_out.sponsors.append(Sponsor(name, None, role))

    # --- eligibility ------------------------------------------------------
    raw_sex = _get(record, "sex")
    raw_age = _get(record, "age")
    if raw_sex is not None or raw_age is not None:
        try:
            sex = cleaners.parse_sex(raw_sex)
        except ValueError:
            issues.append(DQIssue("sex", "unknown_value", str(raw_sex), "set_null", nct_id))
            sex = None

        # Age cells look like: "18 Years and older (Adult, Older Adult)"
        # or "18 Years to 65 Years". I pick out the age-looking pieces.
        import re

        min_age = max_age = None
        if raw_age:
            found = re.findall(r"\d+\s*(?:Year|Month|Week|Day)s?", str(raw_age), re.IGNORECASE)
            try:
                if len(found) >= 1:
                    min_age = cleaners.parse_age_years(found[0])
                if len(found) >= 2:
                    max_age = cleaners.parse_age_years(found[1])
            except ValueError:
                issues.append(DQIssue("age", "invalid_age", str(raw_age), "set_null", nct_id))

        record_out.eligibility = Eligibility(sex, min_age, max_age, None)

    return record_out, issues
