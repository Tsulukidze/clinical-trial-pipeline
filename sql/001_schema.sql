-- ============================================================================
-- Clinical Trial Data Pipeline - Database Schema
-- ============================================================================
-- My main design choices:
--
--   * I use two schemas. `staging` keeps the raw data exactly as it
--     arrived. `clinical` keeps the clean, validated data. If I find a
--     bug in my cleaning logic later, I can fix it and re-run the
--     cleaning from staging without downloading the data again.
--
--   * I normalize the data that repeats a lot. The same condition,
--     sponsor or country appears in thousands of studies, so I store
--     each one once and connect it to studies through link tables.
--     Interventions and outcomes stay attached to their study, because
--     their text is specific to that study.
--
--   * I use the NCT ID as the primary key of studies. It is already
--     a unique ID given by the official registry, so I do not need
--     to invent my own.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS clinical;

-- ----------------------------------------------------------------------------
-- STAGING: raw landing zone
-- ----------------------------------------------------------------------------
-- Every record lands here untouched, saved as JSON.
-- I also save which run and which source it came from, so I can always
-- trace any value back to its origin.
CREATE TABLE IF NOT EXISTS staging.raw_studies (
    id           BIGSERIAL PRIMARY KEY,
    run_id       BIGINT      NOT NULL,
    source       TEXT        NOT NULL,          -- for example 'csv:file.csv' or 'api:clinicaltrials.gov'
    nct_id       VARCHAR(11),                   -- pulled out of the JSON for easy lookups, can be NULL
    payload      JSONB       NOT NULL,          -- the full raw record
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_raw_studies_run    ON staging.raw_studies (run_id);
CREATE INDEX IF NOT EXISTS idx_raw_studies_nct_id ON staging.raw_studies (nct_id);

-- ----------------------------------------------------------------------------
-- PIPELINE METADATA
-- ----------------------------------------------------------------------------
-- One row per pipeline run. This answers: what ran, when, from which
-- source, how many records, and did it succeed.
CREATE TABLE IF NOT EXISTS clinical.ingestion_runs (
    run_id            BIGSERIAL PRIMARY KEY,
    source_name       TEXT        NOT NULL,
    source_type       TEXT        NOT NULL CHECK (source_type IN ('csv', 'json_api', 'sql')),
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
    status            TEXT        NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'success', 'failed')),
    records_extracted INTEGER     DEFAULT 0,
    records_loaded    INTEGER     DEFAULT 0,
    records_rejected  INTEGER     DEFAULT 0,
    error_message     TEXT
);

-- Every data problem I find gets a row here. I never fix data silently:
-- I record what was wrong, what the raw value was, and what I did about it.
CREATE TABLE IF NOT EXISTS clinical.data_quality_issues (
    issue_id    BIGSERIAL PRIMARY KEY,
    run_id      BIGINT REFERENCES clinical.ingestion_runs (run_id),
    nct_id      VARCHAR(11),
    field_name  TEXT NOT NULL,
    issue_type  TEXT NOT NULL,        -- for example 'missing_value' or 'invalid_date'
    raw_value   TEXT,
    action      TEXT NOT NULL,        -- for example 'set_null' or 'rejected_record'
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dq_issues_run  ON clinical.data_quality_issues (run_id);
CREATE INDEX IF NOT EXISTS idx_dq_issues_type ON clinical.data_quality_issues (issue_type);

-- ----------------------------------------------------------------------------
-- CORE: studies (the main table, everything else connects to it)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clinical.studies (
    -- The CHECK makes sure only real NCT IDs get in: the letters "NCT"
    -- followed by exactly 8 digits.
    nct_id                  VARCHAR(11) PRIMARY KEY
                            CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    brief_title             TEXT NOT NULL,
    official_title          TEXT,
    study_type              TEXT,                 -- INTERVENTIONAL / OBSERVATIONAL / ...
    overall_status          TEXT,                 -- COMPLETED / RECRUITING / TERMINATED / ...
    phase                   TEXT,                 -- for example 'PHASE1' or 'PHASE1/PHASE2' or 'NA'
    enrollment              INTEGER CHECK (enrollment >= 0),
    enrollment_type         TEXT CHECK (enrollment_type IN ('ACTUAL', 'ESTIMATED') OR enrollment_type IS NULL),
    start_date              DATE,
    primary_completion_date DATE,
    completion_date         DATE,
    -- Postgres computes this column by itself on every insert/update.
    -- This way my timeline queries do not repeat the same math everywhere.
    duration_days           INTEGER GENERATED ALWAYS AS (completion_date - start_date) STORED,
    why_stopped             TEXT,
    has_results             BOOLEAN DEFAULT FALSE,
    source                  TEXT NOT NULL,        -- which source loaded this row
    first_loaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A study cannot end before it starts. NULL dates are allowed through.
    CONSTRAINT chk_dates CHECK (
        completion_date IS NULL OR start_date IS NULL OR completion_date >= start_date
    )
);

-- I picked these indexes to match the analytics questions from the task:
CREATE INDEX IF NOT EXISTS idx_studies_type_phase ON clinical.studies (study_type, phase); -- trials by type and phase
CREATE INDEX IF NOT EXISTS idx_studies_status     ON clinical.studies (overall_status);    -- completion rates
CREATE INDEX IF NOT EXISTS idx_studies_start_date ON clinical.studies (start_date);        -- timeline analysis

-- ----------------------------------------------------------------------------
-- SHARED REFERENCE TABLES + LINK TABLES (many-to-many)
-- ----------------------------------------------------------------------------
-- One row per unique condition (disease). Studies link to it below.
CREATE TABLE IF NOT EXISTS clinical.conditions (
    condition_id BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE              -- I store it cleaned: trimmed, one casing style
);

CREATE TABLE IF NOT EXISTS clinical.study_conditions (
    nct_id       VARCHAR(11) NOT NULL REFERENCES clinical.studies (nct_id) ON DELETE CASCADE,
    condition_id BIGINT      NOT NULL REFERENCES clinical.conditions (condition_id),
    PRIMARY KEY (nct_id, condition_id)
);
-- Postgres does not index foreign keys by itself, so I add this one for
-- fast "which studies have this condition" lookups.
CREATE INDEX IF NOT EXISTS idx_study_conditions_condition ON clinical.study_conditions (condition_id);

CREATE TABLE IF NOT EXISTS clinical.sponsors (
    sponsor_id   BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    agency_class TEXT                              -- INDUSTRY / NIH / OTHER / ...
);

CREATE TABLE IF NOT EXISTS clinical.study_sponsors (
    nct_id     VARCHAR(11) NOT NULL REFERENCES clinical.studies (nct_id) ON DELETE CASCADE,
    sponsor_id BIGINT      NOT NULL REFERENCES clinical.sponsors (sponsor_id),
    role       TEXT        NOT NULL CHECK (role IN ('LEAD', 'COLLABORATOR')),
    PRIMARY KEY (nct_id, sponsor_id, role)
);
CREATE INDEX IF NOT EXISTS idx_study_sponsors_sponsor ON clinical.study_sponsors (sponsor_id);

CREATE TABLE IF NOT EXISTS clinical.countries (
    country_id BIGSERIAL PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE
);

-- A location is a specific place (hospital, clinic) where a study runs.
-- The facility text is study-specific, but the country is shared, so the
-- "geographic distribution" query can group by a small countries table.
CREATE TABLE IF NOT EXISTS clinical.study_locations (
    location_id BIGSERIAL PRIMARY KEY,
    nct_id      VARCHAR(11) NOT NULL REFERENCES clinical.studies (nct_id) ON DELETE CASCADE,
    facility    TEXT,
    city        TEXT,
    state       TEXT,
    country_id  BIGINT REFERENCES clinical.countries (country_id)
);
CREATE INDEX IF NOT EXISTS idx_study_locations_nct     ON clinical.study_locations (nct_id);
CREATE INDEX IF NOT EXISTS idx_study_locations_country ON clinical.study_locations (country_id);

-- ----------------------------------------------------------------------------
-- STUDY DETAIL TABLES (one-to-many, they belong to one study)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clinical.interventions (
    intervention_id   BIGSERIAL PRIMARY KEY,
    nct_id            VARCHAR(11) NOT NULL REFERENCES clinical.studies (nct_id) ON DELETE CASCADE,
    intervention_type TEXT,                        -- DRUG / DEVICE / BEHAVIORAL / ...
    name              TEXT NOT NULL,
    description       TEXT
);
CREATE INDEX IF NOT EXISTS idx_interventions_nct  ON clinical.interventions (nct_id);
CREATE INDEX IF NOT EXISTS idx_interventions_type ON clinical.interventions (intervention_type);
CREATE INDEX IF NOT EXISTS idx_interventions_name ON clinical.interventions (name);

CREATE TABLE IF NOT EXISTS clinical.outcomes (
    outcome_id   BIGSERIAL PRIMARY KEY,
    nct_id       VARCHAR(11) NOT NULL REFERENCES clinical.studies (nct_id) ON DELETE CASCADE,
    outcome_type TEXT NOT NULL CHECK (outcome_type IN ('PRIMARY', 'SECONDARY', 'OTHER')),
    measure      TEXT NOT NULL,
    time_frame   TEXT
);
CREATE INDEX IF NOT EXISTS idx_outcomes_nct ON clinical.outcomes (nct_id);

-- Who can join the study: sex, age range, healthy volunteers or not.
-- One row per study, so nct_id is both primary key and foreign key.
CREATE TABLE IF NOT EXISTS clinical.eligibility (
    nct_id             VARCHAR(11) PRIMARY KEY REFERENCES clinical.studies (nct_id) ON DELETE CASCADE,
    sex                TEXT CHECK (sex IN ('ALL', 'MALE', 'FEMALE') OR sex IS NULL),
    min_age_years      NUMERIC(5,2) CHECK (min_age_years >= 0),
    max_age_years      NUMERIC(5,2) CHECK (max_age_years >= 0),
    healthy_volunteers BOOLEAN
);

-- Adverse events exist only for studies that reported results.
-- I store one row per (study, event term) with how many people were
-- affected and how many were at risk.
CREATE TABLE IF NOT EXISTS clinical.adverse_events (
    adverse_event_id  BIGSERIAL PRIMARY KEY,
    nct_id            VARCHAR(11) NOT NULL REFERENCES clinical.studies (nct_id) ON DELETE CASCADE,
    event_term        TEXT NOT NULL,
    organ_system      TEXT,
    is_serious        BOOLEAN NOT NULL DEFAULT FALSE,
    subjects_affected INTEGER CHECK (subjects_affected >= 0),
    subjects_at_risk  INTEGER CHECK (subjects_at_risk >= 0)
);
CREATE INDEX IF NOT EXISTS idx_adverse_events_nct  ON clinical.adverse_events (nct_id);
CREATE INDEX IF NOT EXISTS idx_adverse_events_term ON clinical.adverse_events (event_term);
