"""Sample records used by the tests.

I keep them as functions (not constants), so every test gets its own
fresh copy and can safely modify it without affecting other tests.
"""

from __future__ import annotations


def api_payload() -> dict:
    """A realistic ClinicalTrials.gov API v2 study."""
    return {
        "hasResults": True,
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT04280705",
                "briefTitle": "Adaptive COVID-19 Treatment Trial",
                "officialTitle": "A Multicenter, Adaptive, Randomized Blinded Controlled Trial",
            },
            "statusModule": {
                "overallStatus": "COMPLETED",
                "startDateStruct": {"date": "2020-02-21"},
                "primaryCompletionDateStruct": {"date": "2020-05"},  # partial date
                "completionDateStruct": {"date": "2021-04-01"},
            },
            "designModule": {
                "studyType": "INTERVENTIONAL",
                "phases": ["PHASE3"],
                "enrollmentInfo": {"count": 1062, "type": "ACTUAL"},
            },
            "conditionsModule": {"conditions": ["COVID-19", "covid-19"]},  # duplicate
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "NIAID", "class": "NIH"},
                "collaborators": [{"name": "Some University", "class": "OTHER"}],
            },
            "armsInterventionsModule": {
                "interventions": [
                    {"type": "DRUG", "name": "Remdesivir", "description": "IV infusion"}
                ]
            },
            "contactsLocationsModule": {
                "locations": [
                    {
                        "facility": "Mayo Clinic",
                        "city": "Rochester",
                        "state": "Minnesota",
                        "country": "united states",
                    }
                ]
            },
            "outcomesModule": {
                "primaryOutcomes": [{"measure": "Time to recovery", "timeFrame": "Day 29"}]
            },
            "eligibilityModule": {
                "sex": "ALL",
                "minimumAge": "18 Years",
                "maximumAge": "99 Years",
                "healthyVolunteers": False,
            },
        },
    }


def csv_row() -> dict:
    """A realistic Kaggle CSV row, headers already snake_cased
    the way csv_source.py normalizes them."""
    return {
        "nct_number": "NCT04321174",
        "title": "Convalescent Plasma for COVID-19",
        "status": "Completed",
        "study_type": "Interventional",
        "phases": "Phase 2|Phase 3",
        "enrollment": "500",
        "start_date": "May 14, 2020",
        "completion_date": "December 1, 2021",
        "conditions": "COVID-19|Pneumonia, Viral",
        "interventions": "Biological: Convalescent Plasma|Other: Placebo",
        "locations": "Hamilton Health Sciences, Hamilton, Ontario, Canada",
        "sponsor_collaborators": "McMaster University|Canadian Blood Services",
        "gender": "All",
        "age": "18 Years and older   (Adult, Older Adult)",
    }
