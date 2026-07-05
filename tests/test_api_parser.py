"""Tests for the API parser: nested JSON in, clean StudyRecord out."""

from datetime import date

from pipeline.transform.api_parser import parse_api_record
from tests.samples import api_payload


def test_happy_path_core_fields():
    record, issues = parse_api_record(api_payload())
    assert record is not None
    assert record.nct_id == "NCT04280705"
    assert record.brief_title == "Adaptive COVID-19 Treatment Trial"
    assert record.study_type == "INTERVENTIONAL"
    assert record.overall_status == "COMPLETED"
    assert record.phase == "PHASE3"
    assert record.enrollment == 1062
    assert record.enrollment_type == "ACTUAL"
    assert record.start_date == date(2020, 2, 21)
    assert record.has_results is True


def test_partial_date_is_kept_but_logged():
    record, issues = parse_api_record(api_payload())
    # 2020-05 becomes 2020-05-01 ...
    assert record.primary_completion_date == date(2020, 5, 1)
    # ... and the approximation is on record
    partial = [i for i in issues if i.issue_type == "partial_date"]
    assert len(partial) == 1
    assert partial[0].field_name == "primary_completion_date"


def test_duplicate_conditions_collapse_to_one():
    record, _ = parse_api_record(api_payload())
    # the payload contains 'COVID-19' and 'covid-19'
    assert record.conditions == ["Covid-19"]


def test_sponsors_lead_and_collaborator_roles():
    record, _ = parse_api_record(api_payload())
    roles = [(s.name, s.role) for s in record.sponsors]
    assert roles == [("NIAID", "LEAD"), ("Some University", "COLLABORATOR")]


def test_country_casing_is_normalized():
    record, _ = parse_api_record(api_payload())
    assert record.locations[0].country == "United States"


def test_eligibility_is_parsed():
    record, _ = parse_api_record(api_payload())
    assert record.eligibility.sex == "ALL"
    assert record.eligibility.min_age_years == 18.0
    assert record.eligibility.max_age_years == 99.0
    assert record.eligibility.healthy_volunteers is False


def test_completion_before_start_is_dropped_and_logged():
    payload = api_payload()
    payload["protocolSection"]["statusModule"]["completionDateStruct"] = {"date": "2019-01-01"}
    record, issues = parse_api_record(payload)
    assert record.completion_date is None
    assert any(i.issue_type == "completion_before_start" for i in issues)


def test_invalid_enrollment_is_nulled_and_logged():
    payload = api_payload()
    payload["protocolSection"]["designModule"]["enrollmentInfo"]["count"] = "-10"
    record, issues = parse_api_record(payload)
    assert record.enrollment is None
    assert any(i.issue_type == "invalid_enrollment" for i in issues)


def test_bad_nct_id_rejects_record():
    payload = api_payload()
    payload["protocolSection"]["identificationModule"]["nctId"] = "BAD_ID"
    record, issues = parse_api_record(payload)
    assert record is None
    assert issues[0].issue_type == "invalid_nct_id"
    assert issues[0].action == "rejected_record"


def test_missing_title_rejects_record():
    payload = api_payload()
    del payload["protocolSection"]["identificationModule"]["briefTitle"]
    record, issues = parse_api_record(payload)
    assert record is None
    assert issues[0].field_name == "brief_title"
    assert issues[0].action == "rejected_record"


def test_empty_payload_is_rejected_not_crashing():
    record, issues = parse_api_record({})
    assert record is None
    assert issues[0].action == "rejected_record"
