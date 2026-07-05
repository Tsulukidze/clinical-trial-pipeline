"""Parser for the ClinicalTrials.gov API v2 format.

The API returns deeply nested JSON. This module walks that structure
and produces one clean StudyRecord, collecting a DQIssue for every
problem it meets on the way.
"""

from __future__ import annotations

from pipeline.transform import cleaners
from pipeline.transform.models import (
    DQIssue,
    Eligibility,
    Intervention,
    Location,
    Outcome,
    Sponsor,
    StudyRecord,
)


def _module(payload: dict, name: str) -> dict:
    """Shortcut to one module inside protocolSection. Missing -> {}."""
    return payload.get("protocolSection", {}).get(name, {}) or {}


def _parse_date_field(
    raw: object, field: str, nct_id: str, issues: list[DQIssue]
):
    """Parse one date field and log issues instead of crashing.

    Two possible problems:
      * the date is partial ('2020-01') -> I keep the approximate date
        and log it, so nobody mistakes it for an exact day
      * the date is garbage -> I set None and log it
    """
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


def parse_api_record(payload: dict) -> tuple[StudyRecord | None, list[DQIssue]]:
    """Turn one raw API study into a clean StudyRecord.

    Returns (record, issues). If the record is unusable (no valid
    NCT ID or no title), record is None and the issues explain why.
    """
    issues: list[DQIssue] = []

    ident = _module(payload, "identificationModule")
    status = _module(payload, "statusModule")
    design = _module(payload, "designModule")

    # --- identity checks: without these the record is useless ---------
    try:
        nct_id = cleaners.validate_nct_id(ident.get("nctId"))
    except ValueError as exc:
        issues.append(DQIssue("nct_id", "invalid_nct_id", str(ident.get("nctId")), "rejected_record"))
        return None, issues

    brief_title = cleaners.clean_text(ident.get("briefTitle"))
    if brief_title is None:
        issues.append(DQIssue("brief_title", "missing_value", None, "rejected_record", nct_id))
        return None, issues

    # --- enrollment ----------------------------------------------------
    enrollment_info = design.get("enrollmentInfo", {}) or {}
    try:
        enrollment = cleaners.parse_enrollment(enrollment_info.get("count"))
    except ValueError:
        issues.append(
            DQIssue("enrollment", "invalid_enrollment", str(enrollment_info.get("count")), "set_null", nct_id)
        )
        enrollment = None

    # --- dates ----------------------------------------------------------
    start_date = _parse_date_field(
        (status.get("startDateStruct") or {}).get("date"), "start_date", nct_id, issues
    )
    primary_completion_date = _parse_date_field(
        (status.get("primaryCompletionDateStruct") or {}).get("date"),
        "primary_completion_date", nct_id, issues,
    )
    completion_date = _parse_date_field(
        (status.get("completionDateStruct") or {}).get("date"), "completion_date", nct_id, issues
    )

    # A study cannot end before it starts. I trust the start date more
    # (it is usually known exactly) and drop the completion date.
    if start_date and completion_date and completion_date < start_date:
        issues.append(
            DQIssue("completion_date", "completion_before_start",
                    str(completion_date), "set_null", nct_id)
        )
        completion_date = None

    record = StudyRecord(
        nct_id=nct_id,
        brief_title=brief_title,
        official_title=cleaners.clean_text(ident.get("officialTitle")),
        study_type=cleaners.standardize_enum(design.get("studyType")),
        overall_status=cleaners.standardize_enum(status.get("overallStatus")),
        phase=cleaners.standardize_phase(design.get("phases")),
        enrollment=enrollment,
        enrollment_type=cleaners.standardize_enum(enrollment_info.get("type")),
        start_date=start_date,
        primary_completion_date=primary_completion_date,
        completion_date=completion_date,
        why_stopped=cleaners.clean_text(status.get("whyStopped")),
        has_results=bool(payload.get("hasResults", False)),
    )

    # --- conditions -------------------------------------------------------
    for raw in _module(payload, "conditionsModule").get("conditions", []) or []:
        name = cleaners.clean_text(raw)
        if name and name.title() not in record.conditions:
            # .title() gives one casing style, so 'covid-19' and
            # 'COVID-19' do not become two different conditions
            record.conditions.append(name.title())

    # --- sponsors -----------------------------------------------------------
    sponsors_module = _module(payload, "sponsorCollaboratorsModule")
    lead = sponsors_module.get("leadSponsor") or {}
    lead_name = cleaners.clean_text(lead.get("name"))
    if lead_name:
        record.sponsors.append(
            Sponsor(lead_name, cleaners.standardize_enum(lead.get("class")), "LEAD")
        )
    for collab in sponsors_module.get("collaborators", []) or []:
        name = cleaners.clean_text(collab.get("name"))
        if name:
            record.sponsors.append(
                Sponsor(name, cleaners.standardize_enum(collab.get("class")), "COLLABORATOR")
            )

    # --- interventions ---------------------------------------------------
    arms = _module(payload, "armsInterventionsModule")
    for item in arms.get("interventions", []) or []:
        name = cleaners.clean_text(item.get("name"))
        if name:
            record.interventions.append(
                Intervention(
                    intervention_type=cleaners.standardize_enum(item.get("type")),
                    name=name,
                    description=cleaners.clean_text(item.get("description")),
                )
            )

    # --- locations -----------------------------------------------------------
    contacts = _module(payload, "contactsLocationsModule")
    for loc in contacts.get("locations", []) or []:
        country = cleaners.clean_text(loc.get("country"))
        record.locations.append(
            Location(
                facility=cleaners.clean_text(loc.get("facility")),
                city=cleaners.clean_text(loc.get("city")),
                state=cleaners.clean_text(loc.get("state")),
                country=country.title() if country else None,
            )
        )

    # --- outcomes ----------------------------------------------------------
    outcomes_module = _module(payload, "outcomesModule")
    outcome_groups = [
        ("PRIMARY", outcomes_module.get("primaryOutcomes")),
        ("SECONDARY", outcomes_module.get("secondaryOutcomes")),
        ("OTHER", outcomes_module.get("otherOutcomes")),
    ]
    for outcome_type, group in outcome_groups:
        for item in group or []:
            measure = cleaners.clean_text(item.get("measure"))
            if measure:
                record.outcomes.append(
                    Outcome(outcome_type, measure, cleaners.clean_text(item.get("timeFrame")))
                )

    # --- eligibility ------------------------------------------------------
    elig = _module(payload, "eligibilityModule")
    if elig:
        try:
            sex = cleaners.parse_sex(elig.get("sex"))
        except ValueError:
            issues.append(DQIssue("sex", "unknown_value", str(elig.get("sex")), "set_null", nct_id))
            sex = None

        ages: dict[str, float | None] = {}
        for field, key in (("min_age_years", "minimumAge"), ("max_age_years", "maximumAge")):
            try:
                ages[field] = cleaners.parse_age_years(elig.get(key))
            except ValueError:
                issues.append(DQIssue(field, "invalid_age", str(elig.get(key)), "set_null", nct_id))
                ages[field] = None

        record.eligibility = Eligibility(
            sex=sex,
            min_age_years=ages["min_age_years"],
            max_age_years=ages["max_age_years"],
            healthy_volunteers=cleaners.parse_bool(elig.get("healthyVolunteers")),
        )

    return record, issues
