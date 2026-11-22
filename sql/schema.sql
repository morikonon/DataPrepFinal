-- One row per course per day: the daily DAG adds a new snapshot, a rerun on the same day overwrites it.

CREATE TABLE IF NOT EXISTS courses (
    course_id        INTEGER NOT NULL CHECK (course_id > 0),
    snapshot_date    TEXT    NOT NULL,                      -- YYYY-MM-DD, Asia/Almaty
    difficulty       TEXT    NOT NULL CHECK (difficulty IN ('beginner', 'intermediate', 'advanced')),
    catalog_rank     INTEGER NOT NULL CHECK (catalog_rank >= 1), -- position in the popularity-sorted listing
    title            TEXT    NOT NULL,
    authors          TEXT    NOT NULL,                      -- '; '-separated, 'Unknown' if none
    authors_count    INTEGER NOT NULL CHECK (authors_count >= 0),
    summary          TEXT,
    rating           REAL    CHECK (rating BETWEEN 1 AND 5),  -- NULL: no reviews yet
    reviews_count    INTEGER NOT NULL CHECK (reviews_count >= 0),
    learners_count   INTEGER NOT NULL CHECK (learners_count >= 0),
    workload_hours   INTEGER CHECK (workload_hours > 0),    -- NULL: not stated by the author
    has_certificate  INTEGER NOT NULL CHECK (has_certificate IN (0, 1)),
    is_free          INTEGER NOT NULL CHECK (is_free IN (0, 1)),
    price_rub        REAL    NOT NULL CHECK (price_rub >= 0),
    old_price_rub    REAL    CHECK (old_price_rub > price_rub), -- NULL: no discount
    discount_pct     REAL    NOT NULL CHECK (discount_pct BETWEEN 0 AND 100),
    courses_included INTEGER NOT NULL CHECK (courses_included >= 1), -- > 1 for multi-course programmes
    course_url       TEXT    NOT NULL,
    scraped_at       TEXT    NOT NULL,                      -- ISO 8601 with UTC offset
    PRIMARY KEY (course_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_courses_snapshot ON courses (snapshot_date, difficulty);
