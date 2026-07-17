# Clinical Trial Data Pipeline

A data pipeline that downloads clinical trial data, cleans and validates it,
stores it in a normalized PostgreSQL database, and answers analytical
questions about trials, conditions, interventions and locations.


---

## 1. Project overview

The pipeline works in three stages:

1. **Ingest** — read raw data from a source (API, CSV file, or SQL database)
   and save it unchanged into a staging table.
2. **Process** — clean and validate the staged records, then load them into
   normalized tables. Every data problem found is recorded, never hidden.
3. **Report** — run SQL analytics on the clean data.

### Architecture diagram

```
       SOURCES                 STAGING                  CLINICAL                ANALYTICS
 ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────────┐   ┌──────────────────┐
 │ ClinicalTrials.gov│   │                  │   │ studies              │   │ trials by phase  │
 │ API v2 (JSON)     ├──►│ staging.         │   │ conditions + links   │   │ top conditions   │
 │                   │   │ raw_studies      ├──►│ sponsors  + links    ├──►│ completion rates │
 │ CSV files         ├──►│                  │   │ locations, countries │   │ geography        │
 │                   │   │ (raw JSONB,      │   │ interventions        │   │ durations        │
 │ SQL databases     ├──►│  exactly as      │   │ outcomes             │   │ enrollment       │
 │                   │   │  received)       │   │ eligibility          │   │                  │
 └──────────────────┘   └──────────────────┘   └──────────────────────┘   └──────────────────┘
        ingest                                transform + load                   report
   (python -m pipeline      (python -m pipeline process:                  (python -m pipeline
        ingest)              clean, validate, log issues)                       report)

 Every run is tracked in clinical.ingestion_runs.
 Every data problem is tracked in clinical.data_quality_issues.
```

### Dataset choice

I use the ClinicalTrials.gov API v2** as the primary source. The pipeline also ingests **Kaggle CSV exports
** and **external SQL databases**, so multiple source types
from the functional requirements are covered by one architecture: every
source produces the same stream of records, and the rest of the pipeline
does not care where data came from.

### Project structure

```
├── docker-compose.yml        Postgres 16 + pipeline app
├── Dockerfile
├── pyproject.toml            makes the project installable (pip install -e .)
├── requirements.txt
├── .env.example              configuration template (copy to .env)
├── sql/
│   └── 001_schema.sql        full database schema with comments
├── src/pipeline/
│   ├── __main__.py           CLI: init-db, ingest, validate, process, report
│   ├── config.py             settings from environment variables
│   ├── db.py                 engine, transactions, schema applier
│   ├── runs.py               run tracking (start/finish)
│   ├── ingest/               csv_source, api_source, sql_source
│   ├── transform/            cleaners, parsers, validation
│   ├── load/                 staging loader, clinical loader
│   └── analytics/            SQL reports
└── tests/                    unit tests 
```

---

## 2. Setup instructions

### What you need installed

- Docker Desktop
- Python 3.11 or newer

That is all. PostgreSQL itself runs in Docker — no local installation. I used image: postgres:16-alpine  version but you can use newer stable versions as well.

### Steps

```bash
# 1. Clone the repository
git clone <repo-url>
cd clinical-trial-pipeline

# 2. Create your configuration file from the template
cp .env.example .env
# (Windows PowerShell:  Copy-Item .env.example .env)

# IMPORTANT: if you already have PostgreSQL installed locally, port 5432
# is taken. Open .env and change:  POSTGRES_PORT=5433 , in my case I already use POSTGRES_PORT=5433

# 3. Start the database (first start also creates the schema automatically)
docker compose up -d db

# 4. Create a virtual environment and install the project
python -m venv .venv
# activate it: .venv\Scripts\activate (Windows) or source .venv/bin/activate (Linux/Mac)
pip install -e .

# 5. Check the database connection
python -m pipeline init-db
```

### Running the pipeline

```bash
# Ingest 500 COVID-19 studies from the ClinicalTrials.gov API
python -m pipeline ingest --source api --max-records 500 --condition covid-19

# Or ingest a CSV file (put it into data/input/ first)
python -m pipeline ingest --source csv --path data/input/trials.csv

# Optional: dry run — validate staged data and print a data quality report
python -m pipeline validate

# Clean, validate and load into the clinical tables
python -m pipeline process

# Run the analytics
python -m pipeline report   #outputs all 5 reports
python -m pipeline report --name geography --top 5
```

### Running the tests

```bash
pytest
```

62 tests, no database or network needed, finish in about a second.

### Running fully inside Docker

```bash
docker compose build
docker compose run --rm pipeline python -m pipeline ingest --source api --max-records 500
docker compose run --rm pipeline python -m pipeline process
docker compose run --rm pipeline python -m pipeline report
```

---

## 3. Reasoning behind design decisions

**Staging layer with raw JSONB.** Every record is first saved exactly as it
arrived. If I find a bug in my cleaning logic, I fix it and re-run
`process` — no re-downloading. It also gives full lineage: any clean value
can be traced back to its raw original.

**One record format after ingestion.** All three sources produce the same
thing: a stream of dicts. The rest of the pipeline does not know or care
about the source. Adding a fourth source means writing one new file.

**Normalize what repeats, keep the rest simple.** Conditions, sponsors and
countries repeat across thousands of studies, so they live in their own
tables connected through link tables. Interventions and outcomes are
study-specific text, so they stay as child rows of the study.
Over-normalizing them would add joins without benefit.

**NCT ID as primary key.** It is the official, globally unique registry ID.
A regex CHECK constraint (`^NCT[0-9]{8}$`) stops garbage IDs at the
database level even if application code has a bug.

**Errors are data.** The pipeline never fixes anything silently. Every
problem (bad date, negative enrollment, unusable record) becomes a row in
`clinical.data_quality_issues` with the raw value and the action taken.
On my test run of 200 API records, 53 issues were found and logged — all
of them month-precision dates approximated to the first day of the period.

**Validation in Python, integrity in the database.** Python decides what
is clean and logs problems politely. The database CHECK constraints
(enrollment >= 0, completion >= start, valid enum values) are the last
line of defense that holds even if code is buggy.

**Idempotent loading.** Running `process` twice does not duplicate data.
Studies use PostgreSQL upsert (`INSERT ... ON CONFLICT DO UPDATE`), child
rows are deleted and reinserted per study.

**Indexes chosen for the required analytics.** Composite index on
(study_type, phase) for the type/phase question, indexes on status, start
date, and on every foreign key of the link tables (Postgres does not
create those automatically).

**SQL for analytics, not pandas.** The six reports are plain SQL using
CTEs, `FILTER` aggregates, window functions and `percentile_cont` for
medians. Set-based analytical work belongs in the database.

---

## 4. Trade-offs and limitations

- **CSV location parsing is approximate.** The `Facility, City, State,
  Country` cell is ambiguous because facility names can contain commas.
  I only trust the edges (first part = facility, last part = country).
- **Child rows use delete-and-reinsert** instead of diffing. Simpler and
  correct; slightly more writes on re-processing. Fine at this scale.
- **Adverse events table exists in the schema but is not populated.**
  The API only provides adverse events for studies with posted results,
  through a separate results section. The model is ready; the parser for
  that section is future work.
- **Partial dates are approximated** to the first day of their period
  (2020-05 becomes 2020-05-01). Necessary for timeline analytics; every
  approximation is logged so it is never mistaken for a real day.
- **No orchestrator (Airflow/Prefect).** For a prototype, a CLI with
  clear stages is easier to run and review. The stage separation maps
  directly onto orchestrator tasks later.
- **DQ issues accumulate.** Each `process` run logs what it found, so
  reprocessing the same data adds new issue rows (they are facts about a
  run, not about a study). A retention/cleanup policy would be added in
  production.

---

## 5. Time allocation breakdown

Roughly 4.5 hours total:

| Activity | Time |
|---|---|
| Reading the task, choosing dataset, designing schema and architecture | ~1 h |
| Ingestion layer (API client, CSV, SQL) + staging + run tracking | ~1 h |
| Transform and validation (parsers, cleaners, DQ logging) | ~1 h |
| Clinical loader + analytics queries | ~30 min |
| Tests | ~30 min |
| README and documentation | ~40 min |

Environment issues cost extra time (a local PostgreSQL already using port
5432 — solved by making the port configurable), which is reflected in the
setup instructions above.

---

## 6. Future improvements and scalability considerations

- Incremental loading: ask the API only for studies updated since the
  last successful run (the API supports filtering) instead of full pulls.
- Parse the results section of the API to populate adverse events —
  the schema is already prepared for it.
- Integration tests against a throwaway dockerized Postgres
  (testcontainers) in addition to the unit tests.
- An orchestrator (Airflow or Prefect) once schedules and dependencies
  appear; the ingest/process/report stages map directly onto tasks.
- A small Streamlit or FastAPI layer on top of the analytics queries.
- CI pipeline (GitHub Actions): run pytest and a docker build on every push.

---

## 7. Bonus questions

### Scalability: how would I handle 100x more data volume?

The pipeline already streams everything (generators, batched inserts), so
memory use is flat regardless of volume — the current design survives 100x
without code changes, just slower. To make it fast as well:
`COPY`-based bulk loading instead of row inserts (about 10x faster),
incremental ingestion instead of full pulls, partitioning large tables by
date, and parallel ingestion workers. At much larger scale I would move
raw staging to object storage (S3) with a warehouse (BigQuery/Snowflake)
or Spark for transformation — the staged-raw / clean-model separation
stays exactly the same.

### Data quality: what additional validation rules would I implement?

Cross-field rules (a COMPLETED study should have a completion date; a
TERMINATED study should have why_stopped), referential checks against
official vocabularies (MedDRA for conditions, ISO 3166 for countries),
statistical outlier detection (enrollment of 10 million is probably a
typo), duplicate detection beyond the NCT ID (same title + sponsor +
dates), and freshness checks (warn if a recruiting study has not been
updated in years).

### Compliance: what would a GxP environment require?

A validated system: documented requirements, risk assessment, and a
qualification package (IQ/OQ/PQ) proving the pipeline does what it claims.
Full audit trail — who changed what, when, why (the run tracking and DQ
tables here are a small start; GxP needs immutable, tamper-evident logs
including schema changes). Change control for every code and schema
modification, electronic signatures where records are approved
(21 CFR Part 11), qualified/pinned software versions, and documented
data retention and disaster recovery procedures.

### Monitoring: how would I monitor this pipeline in production?

The `ingestion_runs` table is already the core: every run with status,
counts and errors. In production I would add alerts on failed runs and on
runs that load zero records, metrics (records/second, run duration,
DQ issues per run) exported to Prometheus/Grafana or CloudWatch,
anomaly-based alerts (today's volume is 50% below the average — the
source may be broken), data freshness checks on the clinical tables, and
a dead-letter review process for rejected records.

### Security: what would I implement for sensitive clinical data?

This dataset is public registry metadata, but for real patient-level data:
encryption in transit (TLS to the database, already standard) and at rest,
secrets in a vault (AWS Secrets Manager / Azure Key Vault) instead of .env
files, least-privilege database roles (the pipeline writes, analysts get a
read-only role, nobody shares the superuser), network isolation (database
not exposed publicly), row-level security or column masking for personally
identifiable fields, audit logging of access, and pseudonymization of
patient identifiers before data ever enters the analytics layer. The
container already runs as a non-root user; images would also be scanned
for vulnerabilities in CI.

---

## Requirements coverage checklist

| Requirement | Where |
|---|---|
| Multiple sources (CSV, JSON API, SQL) | `src/pipeline/ingest/` — three sources, one interface |
| Processing and validation | `src/pipeline/transform/` — cleaners, parsers, DQ logging |
| Efficient storage | `sql/001_schema.sql` — normalized schema, constraints, indexes |
| Analytics and reporting | `src/pipeline/analytics/` — six SQL reports |
| Python development | typed, modular package, installable, CLI |
| SQL proficiency | upserts, CTEs, FILTER, window functions, percentile_cont |
| Docker | docker-compose with healthcheck, auto schema init, non-root image |
| Testing | 62 unit tests, `pytest` |
| Documentation | this README + comments throughout the code |
