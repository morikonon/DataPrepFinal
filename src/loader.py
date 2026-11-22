"""Creates the SQLite table from sql/schema.sql, upserts a cleaned snapshot and verifies what was stored."""

import argparse
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd

from src import config
from src.cleaner import COLUMNS, latest

log = logging.getLogger("loader")

KEY = ("course_id", "snapshot_date")
VALUE_COLUMNS = [column for column in COLUMNS if column not in KEY]
# A rerun on the same day updates a row only if one of its values changed, so the counts below stay honest.
UPSERT_SQL = (
    f"INSERT INTO courses ({', '.join(COLUMNS)}) VALUES ({', '.join(':' + column for column in COLUMNS)}) "
    f"ON CONFLICT ({', '.join(KEY)}) DO UPDATE SET "
    + ", ".join(f"{column} = excluded.{column}" for column in VALUE_COLUMNS)
    + " WHERE " + " OR ".join(f"courses.{column} IS NOT excluded.{column}" for column in VALUE_COLUMNS)
)

CHECKS_SQL = """
SELECT COUNT(*)                                                AS rows_in_snapshot,
       COUNT(DISTINCT difficulty)                              AS difficulty_levels,
       SUM(title = '' OR authors = '' OR course_url NOT LIKE 'https://stepik.org/course/%') AS broken_rows,
       (SELECT COUNT(DISTINCT snapshot_date) FROM courses)     AS snapshots_in_table
FROM courses WHERE snapshot_date = :day
"""
SUMMARY_SQL = """
SELECT difficulty, COUNT(*) AS courses, SUM(is_free) AS free, SUM(has_certificate) AS certificate,
       ROUND(AVG(NULLIF(price_rub, 0))) AS avg_paid_price_rub, ROUND(AVG(rating), 2) AS avg_rating,
       SUM(learners_count) AS learners
FROM courses WHERE snapshot_date = :day GROUP BY difficulty ORDER BY courses DESC
"""
TOP_SQL = """
SELECT course_id, substr(title, 1, 45) AS title, difficulty, learners_count, rating, price_rub
FROM courses WHERE snapshot_date = :day ORDER BY learners_count DESC LIMIT 5
"""


def connect(db_path: Path = config.DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.executescript(config.SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
    return connection


def to_rows(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> dicts of plain Python values (NaN/<NA> -> None, bool -> 0/1 when bound by sqlite3)."""
    return df.astype(object).where(df.notna(), None).to_dict("records")


def load(clean_path: Path | None = None, db_path: Path = config.DB_PATH) -> dict:
    clean_path = clean_path or latest(config.CLEAN_DIR, "courses_clean_*.csv")
    df = pd.read_csv(clean_path, dtype={"workload_hours": "Int64"})[COLUMNS]
    snapshots = df["snapshot_date"].unique()
    if len(snapshots) != 1:
        raise ValueError(f"Expected one snapshot date in {clean_path.name}, found {list(snapshots)}")

    with closing(connect(db_path)) as connection, connection:
        before = connection.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
        changes_before = connection.total_changes
        connection.executemany(UPSERT_SQL, to_rows(df))
        changed = connection.total_changes - changes_before
        total = connection.execute("SELECT COUNT(*) FROM courses").fetchone()[0]

    inserted = total - before
    stats = {"snapshot_date": str(snapshots[0]), "input_rows": len(df), "inserted": inserted,
             "updated": changed - inserted, "unchanged": len(df) - changed, "rows_in_table": total}
    log.info("Loaded %s into %s: %s", clean_path.name, db_path.name, stats)
    return stats


def verify(snapshot_date: str | None = None, db_path: Path = config.DB_PATH) -> dict:
    """Run SQL checks on one snapshot (default: the newest); raise if the stored data is not usable."""
    with closing(sqlite3.connect(db_path)) as connection:
        day = snapshot_date or connection.execute("SELECT MAX(snapshot_date) FROM courses").fetchone()[0]
        checks = pd.read_sql_query(CHECKS_SQL, connection, params={"day": day}).iloc[0].to_dict()
        summary = pd.read_sql_query(SUMMARY_SQL, connection, params={"day": day})
        top = pd.read_sql_query(TOP_SQL, connection, params={"day": day})

    log.info("Snapshot %s checks: %s", day, checks)
    log.info("Courses by difficulty:\n%s", summary.to_string(index=False))
    log.info("Most popular courses:\n%s", top.to_string(index=False))

    problems = []
    if checks["rows_in_snapshot"] < config.MIN_CLEAN_RECORDS:
        problems.append(f"only {checks['rows_in_snapshot']} rows, need {config.MIN_CLEAN_RECORDS}")
    if checks["difficulty_levels"] != len(config.LEVELS):
        problems.append(f"{checks['difficulty_levels']} difficulty levels instead of {len(config.LEVELS)}")
    if checks["broken_rows"]:
        problems.append(f"{checks['broken_rows']} rows with empty title/authors or a wrong URL")
    if problems:
        raise ValueError("Verification failed: " + "; ".join(problems))
    log.info("Verification passed for snapshot %s", day)
    return {key: int(value) for key, value in checks.items()} | {"snapshot_date": day}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clean_csv", nargs="?", type=Path, help="clean file (default: the newest in data/clean)")
    parser.add_argument("--verify-only", action="store_true", help="skip loading, only check the newest snapshot")
    args = parser.parse_args()
    config.setup_logging()
    if not args.verify_only:
        load(args.clean_csv)
    verify()


if __name__ == "__main__":
    main()
