"""Tests for the CSV parser: flat Kaggle-style row in, clean StudyRecord out."""

from datetime import date

from pipeline.transform.csv_parser import parse_csv_record
from tests.samples import csv_row


def test_happy_path_core_fields():
    record, issues = parse_csv_record(csv_row())
    assert record is not None
    assert record.nct_id == "NCT04321174"
    assert record.brief_title == "Convalescent Plasma for COVID-19"
    assert record.overall_status == "COMPLETED"
    assert record.phase == "PHASE2/PHASE3"
    assert record.enrollment == 500
    assert record.start_date == date(2020, 5, 14)
    assert record.completion_date == date(2021, 12, 1)


def test_conditions_are_split_and_title_cased():
    record, _ = parse_csv_record(csv_row())
    assert record.conditions == ["Covid-19", "Pneumonia, Viral"]


def test_interventions_type_and_name_are_split():
    record, _ = parse_csv_record(csv_row())
    first = record.interventions[0]
    assert first.intervention_type == "BIOLOGICAL"
    assert first.name == "Convalescent Plasma"
    second = record.interventions[1]
    assert second.intervention_type == "OTHER"
    assert second.name == "Placebo"


def test_intervention_without_type_still_kept():
    row = csv_row()
    row["interventions"] = "Just a name without a type"
    record, _ = parse_csv_record(row)
    assert record.interventions[0].intervention_type is None
    assert record.interventions[0].name == "Just a name without a type"


def test_location_parts_facility_and_country():
    record, _ = parse_csv_record(csv_row())
    loc = record.locations[0]
    assert loc.facility == "Hamilton Health Sciences"
    assert loc.country == "Canada"


def test_location_with_only_two_parts():
    row = csv_row()
    row["locations"] = "Small Clinic, France"
    record, _ = parse_csv_record(row)
    loc = record.locations[0]
    assert loc.facility == "Small Clinic"
    assert loc.country == "France"
    assert loc.city is None


def test_sponsors_first_is_lead_rest_collaborators():
    record, _ = parse_csv_record(csv_row())
    assert record.sponsors[0].name == "McMaster University"
    assert record.sponsors[0].role == "LEAD"
    assert record.sponsors[1].role == "COLLABORATOR"


def test_age_range_text_is_parsed():
    record, _ = parse_csv_record(csv_row())
    assert record.eligibility.sex == "ALL"
    assert record.eligibility.min_age_years == 18.0
    assert record.eligibility.max_age_years is None  # "and older" has no upper bound


def test_age_range_with_upper_bound():
    row = csv_row()
    row["age"] = "18 Years to 65 Years (Adult)"
    record, _ = parse_csv_record(row)
    assert record.eligibility.min_age_years == 18.0
    assert record.eligibility.max_age_years == 65.0


def test_completion_before_start_is_dropped_and_logged():
    row = csv_row()
    row["completion_date"] = "April 1, 2020"  # start is May 14, 2020
    record, issues = parse_csv_record(row)
    assert record.completion_date is None
    assert any(i.issue_type == "completion_before_start" for i in issues)


def test_alias_column_names_are_understood():
    # the same file can call the ID column nct_id instead of nct_number
    row = csv_row()
    row["nct_id"] = row.pop("nct_number")
    record, _ = parse_csv_record(row)
    assert record.nct_id == "NCT04321174"


def test_bad_nct_id_rejects_record():
    row = csv_row()
    row["nct_number"] = "not-an-id"
    record, issues = parse_csv_record(row)
    assert record is None
    assert issues[0].action == "rejected_record"


def test_missing_title_rejects_record():
    row = csv_row()
    row["title"] = None
    record, issues = parse_csv_record(row)
    assert record is None
    assert issues[0].field_name == "brief_title"


def test_bad_date_is_nulled_and_logged_not_crashing():
    row = csv_row()
    row["start_date"] = "sometime soon"
    record, issues = parse_csv_record(row)
    assert record is not None
    assert record.start_date is None
    assert any(i.issue_type == "invalid_date" for i in issues)
