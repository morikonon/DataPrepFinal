# Stepik Course Catalogue: from Website to Database

Final project, Data Collection & Preparation, KBTU.
Authors: Mukhamedali Daniyaruly, Alikhan Tursynkhan.

A daily Apache Airflow pipeline scrapes the Stepik online-course catalogue with Selenium, cleans the cards with
pandas and stores one snapshot per day in SQLite.

```
stepik.org/catalog ──Selenium──> data/raw/courses_raw_<date>.csv ──pandas──> data/clean/courses_clean_<date>.csv
                                                                                   │
           verify (SQL checks) <── data/output.db, table "courses" <──sqlite3 UPSERT──┘

Airflow DAG "stepik_courses_pipeline" (@daily):  scrape >> clean >> load >> verify
```

## 1. Website

[Stepik](https://stepik.org/catalog/search) is an educational platform with thousands of free and paid online
courses. Each course card in the catalogue shows the title, authors, a short summary, rating and number of reviews,
number of learners, workload in hours, whether a certificate is issued, the price (and the old price during a
discount) and, for multi-course programmes, how many courses are included.

Why it needs a browser:

- The catalogue is an Ember.js single-page application. The HTML returned by the server contains no courses at all
  (`curl` gets an empty shell); the cards are built by JavaScript.
- The list is first drawn as 20 empty placeholder cards. A card fetches and shows its content only when it scrolls
  into the viewport (lazy loading), so the page has to be scrolled.
- Pagination is done by the "Далее" (Next) button, which re-renders the list without a page reload. A `?page=N`
  link is ignored on the first load, so pages can only be reached by clicking.

The scraper opens the catalogue once per difficulty filter (`?difficulty=easy|normal|hard` — "Для начинающих",
"Для продолжающих", "Для профи"), so every record also gets a difficulty level that is not printed on the card.
Each listing is sorted by popularity and capped by the site at 10 pages; by default 5 pages × 20 cards × 3 levels =
**300 cards per run**. Only the public catalogue page is opened (no login, `robots.txt` allows `/catalog/search`).

Prices are shown in roubles (₽) and the interface language is Russian, because that is what the site serves.

## 2. Project structure

```
airflow_dag.py      Airflow DAG: scrape >> clean >> load >> verify
run_airflow.sh      starts a local Airflow with this folder as the DAGs folder
sql/schema.sql      table creation script (SQLite)
src/config.py       paths, scraping parameters, logging
src/scraper.py      Selenium + BeautifulSoup scraper
src/cleaner.py      cleaning and preprocessing with pandas
src/loader.py       SQLite schema creation, UPSERT, SQL verification
data/raw/           raw scraped cards, one CSV per day
data/clean/         cleaned records, one CSV per day
data/output.db      SQLite database
logs/               task logs of two successful Airflow runs (JSON lines, as Airflow wrote them)
report/             project report (PDF)
```

## 3. Installation

Requirements: Python 3.12, Google Chrome. Selenium Manager downloads a matching chromedriver (≈10 MB) on the first
run. Tested on macOS with Chrome 158.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install "apache-airflow==3.2.1" --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.2.1/constraints-3.12.txt"
pip install -r requirements.txt
```

## 4. Running the scraping (without Airflow)

Every stage is a module with its own command; run them from the project folder in this order:

```bash
python -m src.scraper
python -m src.cleaner
python -m src.loader
```

| Command | Reads | Writes |
|---|---|---|
| `python -m src.scraper [--pages N]` | stepik.org | `data/raw/courses_raw_<date>.csv` |
| `python -m src.cleaner [raw.csv]` | newest raw file | `data/clean/courses_clean_<date>.csv` |
| `python -m src.loader [clean.csv]` | newest clean file | table `courses` in `data/output.db`, then SQL checks |
| `python -m src.loader --verify-only` | `data/output.db` | SQL checks of the newest snapshot |

`--pages` is the number of pages per difficulty level (default 5, maximum 10). Set `STEPIK_HEADLESS=0` to watch the
browser work.

## 5. Running Airflow

```bash
source .venv/bin/activate
./run_airflow.sh
```

`run_airflow.sh` runs `airflow standalone` (API server, scheduler, DAG processor, triggerer) with:

| Setting | Value |
|---|---|
| `AIRFLOW_HOME` | `./airflow_home` (metadata DB, config and task logs; not in git) |
| DAGs folder | this project folder (`.airflowignore` hides everything except `airflow_dag.py`) |
| example DAGs | off |
| time zone | Asia/Almaty |
| login | disabled for local use (`SIMPLE_AUTH_MANAGER_ALL_ADMINS=True`) |

It also sets `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` and `NO_PROXY=*`: on macOS forked Airflow task processes
crash without them. Any other Airflow command goes through the same script, e.g. `./run_airflow.sh dags list`.

Then open <http://localhost:8080>, find `stepik_courses_pipeline` and switch it on, or from a second terminal:

```bash
./run_airflow.sh dags unpause stepik_courses_pipeline
./run_airflow.sh dags trigger stepik_courses_pipeline
./run_airflow.sh dags list-runs stepik_courses_pipeline
```

With `catchup=False`, switching the DAG on starts one run for the latest day straight away; after that it runs once a
day at 00:00 Asia/Almaty.

| DAG setting | Value | Why |
|---|---|---|
| `schedule` | `@daily` | no more than once per 24 hours |
| `catchup` | `False` | no backfill of past days: a scraper can only see today's catalogue |
| `max_active_runs` | 1 | two runs must not drive Chrome and write the database at the same time |
| `retries` / `retry_delay` | 2 / 5 min | a slow page or network error fails the task, Airflow retries it |
| `execution_timeout` | 30 min | a stuck browser cannot block the next day |

Tasks exchange only file paths and small statistics through XCom. Logs of every attempt are in
`airflow_home/logs/dag_id=stepik_courses_pipeline/` and in the UI (task → Logs); copies of the runs of 22.11.2026 and
23.11.2026 are in `logs/run_<date>/`.

## 6. Database

Created by `sql/schema.sql` (executed by the loader before every load).

Table `courses`, primary key `(course_id, snapshot_date)` — one row per course per day. The load is an UPSERT in a
single transaction: a new day adds a snapshot, a rerun or retry on the same day updates only the rows whose values
changed and never creates duplicates.

| Column | Type | Meaning |
|---|---|---|
| `course_id` | INTEGER | Stepik course id (from `/course/<id>`) |
| `snapshot_date` | TEXT | day of scraping, `YYYY-MM-DD` |
| `difficulty` | TEXT | `beginner`, `intermediate`, `advanced` (catalogue filter) |
| `catalog_rank` | INTEGER | position in the popularity-sorted listing of its level |
| `title`, `authors`, `summary` | TEXT | normalised text; authors separated by `; ` |
| `authors_count` | INTEGER | number of authors |
| `rating` | REAL | 1–5, NULL when the course has no reviews |
| `reviews_count`, `learners_count` | INTEGER | `26.1K` → 26100, `1.6M` → 1600000 |
| `workload_hours` | INTEGER | NULL when the author did not state it |
| `has_certificate`, `is_free` | INTEGER | 0/1 flags |
| `price_rub`, `old_price_rub` | REAL | current price; old price only during a discount |
| `discount_pct` | REAL | 0 without a discount |
| `courses_included` | INTEGER | 1 for a course, more for a programme |
| `course_url`, `scraped_at` | TEXT | canonical URL, scraping time (ISO 8601) |

Example query:

```bash
sqlite3 -header -column data/output.db "SELECT difficulty, COUNT(*) AS courses, SUM(is_free) AS free, ROUND(AVG(rating), 2) AS avg_rating FROM courses WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM courses) GROUP BY difficulty"
```

## 7. Expected output

After one run (about 4 minutes, most of it scrolling):

- `data/raw/courses_raw_<date>.csv` — 300 rows, 15 raw text columns (`level`, `page`, `position`, `course_url`,
  `title`, `authors`, `summary`, `rating_text`, `learners_text`, `workload_text`, `certificate_text`, `price_text`,
  `old_price_text`, `badge_text`, `scraped_at`);
- `data/clean/courses_clean_<date>.csv` — about 300 rows (at least 200 is enforced), 20 typed columns;
- `data/output.db` — table `courses` with one snapshot per day;
- the `verify` task log ends with `Verification passed for snapshot <date>` and prints row counts, a summary per
  difficulty level and the five courses with the most learners.

Typical scraper log:

```
INFO | scraper | Scraping https://stepik.org/catalog/search: 3 levels x 5 pages
INFO | scraper | Level easy   page 1: 20 cards, 0 placeholders did not load
...
INFO | scraper | Scraped 300 cards in 248 s -> data/raw/courses_raw_2026-11-22.csv
```
