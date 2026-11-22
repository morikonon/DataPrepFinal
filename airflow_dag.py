"""Daily pipeline: scrape the Stepik catalogue -> clean -> load into SQLite -> verify the stored snapshot."""

from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.sdk import dag, task

DEFAULT_ARGS = {
    "owner": "dcap-final",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=30),
}


@dag(
    dag_id="stepik_courses_pipeline",
    description="Stepik course catalogue: Selenium scraping -> pandas cleaning -> SQLite",
    schedule="@daily",
    start_date=pendulum.datetime(2026, 11, 20, tz="Asia/Almaty"),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["final-project", "selenium", "sqlite"],
)
def stepik_courses_pipeline():
    # Project modules are imported inside the tasks so the scheduler parses this file without loading Selenium/pandas.

    @task
    def scrape() -> str:
        from src import scraper
        return str(scraper.scrape())

    @task
    def clean(raw_path: str) -> str:
        from src import cleaner
        return str(cleaner.run(Path(raw_path)))

    @task
    def load(clean_path: str) -> dict:
        from src import loader
        return loader.load(Path(clean_path))

    @task
    def verify(load_stats: dict) -> dict:
        from src import loader
        return loader.verify(load_stats["snapshot_date"])

    verify(load(clean(scrape())))


stepik_courses_pipeline()
