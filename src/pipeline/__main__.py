"""Command line entry point.

Run with:  python -m pipeline <command> [options]

Commands so far:
    init-db   apply the SQL schema (safe to run more than once)
    ingest    read data from a source and land it in staging

Examples:
    python -m pipeline init-db
    python -m pipeline ingest --source csv --path data/input/trials.csv
    python -m pipeline ingest --source api --max-records 500
    python -m pipeline ingest --source api --max-records 500 --condition covid-19
    python -m pipeline ingest --source sql --url sqlite:///demo.db --query "SELECT * FROM trials"
"""

from __future__ import annotations

import argparse
import logging
import sys

from collections import Counter

from pipeline import db, runs
from pipeline.ingest.api_source import fetch_api_records
from pipeline.ingest.csv_source import read_csv_records
from pipeline.ingest.sql_source import read_sql_records
from pipeline.load.staging import load_to_staging
from pipeline.load.clinical_loader import process_staged
from pipeline.transform.transformer import transform_staged
from pipeline.analytics.queries import REPORTS, get_report, run_report

logger = logging.getLogger("pipeline")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
    )


def cmd_init_db(_: argparse.Namespace) -> int:
    """Apply the schema. Every statement uses IF NOT EXISTS, so
    running this twice does no harm."""
    if not db.healthcheck():
        logger.error("Database is not reachable. Is it running? (docker compose up -d db)")
        return 1
    db.apply_schema("sql")
    logger.info("Schema applied")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Read records from the chosen source and save them to staging."""
    if not db.healthcheck():
        logger.error("Database is not reachable. Is it running? (docker compose up -d db)")
        return 1

    # Pick the source. Each one gives me the same thing: a stream of dicts.
    if args.source == "csv":
        if not args.path:
            logger.error("--path is required for the csv source")
            return 1
        source_name = f"csv:{args.path}"
        records = read_csv_records(args.path)
    elif args.source == "api":
        source_name = "api:clinicaltrials.gov"
        records = fetch_api_records(
            max_records=args.max_records, query_condition=args.condition
        )
    elif args.source == "sql":
        if not args.url or not args.query:
            logger.error("--url and --query are required for the sql source")
            return 1
        source_name = "sql:external"
        records = read_sql_records(args.url, args.query)
    else:  # argparse choices should prevent this, but I check anyway
        logger.error("Unknown source: %s", args.source)
        return 1

    run_id = runs.start_run(source_name, {"csv": "csv", "api": "json_api", "sql": "sql"}[args.source])

    try:
        staged = load_to_staging(run_id, source_name, records)
    except Exception as exc:  # any failure: close the run as failed, then exit
        logger.exception("Ingestion failed")
        runs.finish_run(run_id, "failed", error_message=str(exc))
        return 1

    runs.finish_run(run_id, "success", records_extracted=staged, records_loaded=staged)
    logger.info("Ingestion done: %d records staged (run %d)", staged, run_id)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Dry run of the transform step: clean everything in staging and
    print a report, but write nothing. Lets me check the data quality
    of a run before loading it into the clinical tables."""
    if not db.healthcheck():
        logger.error("Database is not reachable. Is it running? (docker compose up -d db)")
        return 1

    valid = 0
    rejected = 0
    issue_counts: Counter[str] = Counter()

    for record, issues in transform_staged(args.run_id):
        if record is None:
            rejected += 1
        else:
            valid += 1
        for issue in issues:
            issue_counts[f"{issue.field_name}: {issue.issue_type}"] += 1

    print()
    print("=== Validation report ===")
    print(f"Valid records:    {valid}")
    print(f"Rejected records: {rejected}")
    print()
    if issue_counts:
        print("Data quality issues found:")
        for name, count in issue_counts.most_common():
            print(f"  {count:6d}  {name}")
    else:
        print("No data quality issues found.")
    return 0


def cmd_process(args: argparse.Namespace) -> int:
    """Transform staged records and load them into the clinical tables.
    This is the real run; `validate` is its dry-run twin."""
    if not db.healthcheck():
        logger.error("Database is not reachable. Is it running? (docker compose up -d db)")
        return 1

    try:
        stats = process_staged(args.run_id)
    except Exception:
        logger.exception("Processing failed")
        return 1

    print()
    print("=== Processing report ===")
    print(f"Studies loaded:   {stats['loaded']}")
    print(f"Records rejected: {stats['rejected']}")
    print(f"Issues recorded:  {stats['issues']}  (see clinical.data_quality_issues)")
    return 0


def _print_table(columns: list[str], rows: list[tuple]) -> None:
    """Print rows as a simple aligned text table. No extra libraries."""
    if not rows:
        print("  (no data)")
        return
    # Column width = the widest value in that column (or the header).
    cells = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [
        max(len(columns[i]), max(len(row[i]) for row in cells))
        for i in range(len(columns))
    ]
    header = "  ".join(col.ljust(widths[i]) for i, col in enumerate(columns))
    print(header)
    print("-" * len(header))
    for row in cells:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(row))))


def cmd_report(args: argparse.Namespace) -> int:
    """Run the analytics queries and print the results."""
    if not db.healthcheck():
        logger.error("Database is not reachable. Is it running? (docker compose up -d db)")
        return 1

    if args.name:
        report = get_report(args.name)
        if report is None:
            logger.error("Unknown report: %s. Available: %s",
                         args.name, ", ".join(r.key for r in REPORTS))
            return 1
        selected = [report]
    else:
        selected = REPORTS  # no name given: run everything

    for report in selected:
        print()
        print(f"=== {report.title} ===")
        columns, rows = run_report(report, top=args.top)
        _print_table(columns, rows)
    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline", description="Clinical trial data pipeline"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="apply the database schema")
    p_init.set_defaults(func=cmd_init_db)

    p_ingest = sub.add_parser("ingest", help="ingest data from a source into staging")
    p_ingest.add_argument(
        "--source", choices=["csv", "api", "sql"], required=True,
        help="where the data comes from",
    )
    p_ingest.add_argument("--path", help="path to the CSV file (csv source)")
    p_ingest.add_argument(
        "--max-records", type=int, default=1000,
        help="maximum number of studies to download (api source, default 1000)",
    )
    p_ingest.add_argument(
        "--condition", help="optional condition filter, e.g. covid-19 (api source)"
    )
    p_ingest.add_argument("--url", help="SQLAlchemy connection URL (sql source)")
    p_ingest.add_argument("--query", help="SELECT query to run (sql source)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_validate = sub.add_parser(
        "validate", help="clean staged records and print a report (writes nothing)"
    )
    p_validate.add_argument(
        "--run-id", type=int, default=None,
        help="check only one ingestion run (default: all of staging)",
    )
    p_validate.set_defaults(func=cmd_validate)

    p_process = sub.add_parser(
        "process", help="transform staged records and load them into clinical tables"
    )
    p_process.add_argument(
        "--run-id", type=int, default=None,
        help="process only one ingestion run (default: all of staging)",
    )
    p_process.set_defaults(func=cmd_process)

    p_report = sub.add_parser("report", help="run analytics queries and print results")
    p_report.add_argument(
        "--name", default=None,
        help="run one report only: type_phase, conditions, completion, "
             "geography, timeline, enrollment (default: all)",
    )
    p_report.add_argument(
        "--top", type=int, default=10,
        help="row limit for top-N reports like conditions and geography (default 10)",
    )
    p_report.set_defaults(func=cmd_report)

    return parser


def main() -> int:
    _setup_logging()
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
