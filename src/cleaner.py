"""Cleans raw catalogue cards: drops empty and duplicate cards, normalises text, converts types, fills gaps."""

import argparse
import logging
import re
import unicodedata
from pathlib import Path

import pandas as pd

from src import config

log = logging.getLogger("cleaner")

PAGE_SIZE = 20
DIFFICULTY = {"Для начинающих": "beginner", "Для продолжающих": "intermediate", "Для профи": "advanced"}
INVISIBLE = re.compile(r"[​-‍⁠﻿]")
EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️]")
QUOTES = str.maketrans("«»“”„", '"""""')
MULTIPLIER = {"": 1, "K": 1_000, "M": 1_000_000}

COLUMNS = [
    "course_id", "snapshot_date", "difficulty", "catalog_rank", "title", "authors", "authors_count", "summary",
    "rating", "reviews_count", "learners_count", "workload_hours", "has_certificate", "is_free", "price_rub",
    "old_price_rub", "discount_pct", "courses_included", "course_url", "scraped_at",
]


def normalize_text(value) -> str | None:
    if pd.isna(value):
        return None
    text = unicodedata.normalize("NFKC", str(value))  # also turns non-breaking spaces into plain ones
    text = EMOJI.sub("", INVISIBLE.sub("", text)).translate(QUOTES)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def normalize_authors(value) -> str | None:
    if pd.isna(value):
        return None
    names = (normalize_text(name) for name in str(value).split("|"))
    return "; ".join(dict.fromkeys(name for name in names if name)) or None


def parse_count(series: pd.Series) -> pd.Series:
    """'1.6M' -> 1600000, '26.1K' -> 26100, '208' -> 208 (the site rounds large numbers)."""
    parts = series.str.upper().str.extract(r"(\d+(?:\.\d+)?)\s*([KM]?)")
    return (pd.to_numeric(parts[0]) * parts[1].map(MULTIPLIER)).round().astype("Int64")


def parse_price(series: pd.Series) -> pd.Series:
    """'9 200 ₽' -> 9200.0, 'Бесплатно' -> 0.0."""
    free = series.str.contains("бесплатно", case=False, na=False)
    amount = pd.to_numeric(series.str.replace(r"[^\d,.]", "", regex=True).str.replace(",", "."), errors="coerce")
    return amount.mask(free, 0.0)


def drop_empty_cards(df: pd.DataFrame) -> pd.DataFrame:
    kept = df.dropna(subset=["course_url", "title"])
    log.info("Empty placeholder cards removed: %d", len(df) - len(kept))
    return kept


def remove_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """A course is stored once per snapshot; if it was seen twice, keep its best catalogue position."""
    df = df.assign(
        course_id=df["course_url"].str.extract(r"/course/(\d+)")[0].astype("Int64"),
        catalog_rank=(df["page"] - 1) * PAGE_SIZE + df["position"],
    )
    deduplicated = df.sort_values("catalog_rank").drop_duplicates("course_id", keep="first")
    log.info("Duplicate course cards removed: %d", len(df) - len(deduplicated))
    return deduplicated


def normalize_text_fields(df: pd.DataFrame) -> pd.DataFrame:
    changed = {}
    for column in ("title", "summary"):
        cleaned = df[column].map(normalize_text)
        changed[column] = int((cleaned.fillna("") != df[column].fillna("")).sum())
        df[column] = cleaned
    df["authors"] = df["authors"].map(normalize_authors)
    df["authors_count"] = df["authors"].str.count(";").add(1).fillna(0).astype(int)
    df["difficulty"] = df["level"].map(DIFFICULTY)
    log.info("Text normalised (values changed): %s", changed)
    return df


def convert_types(df: pd.DataFrame) -> pd.DataFrame:
    rating = df["rating_text"].str.extract(r"(\d+(?:[.,]\d+)?)\s*\(([^)]+)\)")
    scraped_at = pd.to_datetime(df["scraped_at"])
    df = df.assign(
        rating=pd.to_numeric(rating[0].str.replace(",", ".")),
        reviews_count=parse_count(rating[1]),
        learners_count=parse_count(df["learners_text"]),
        workload_hours=pd.to_numeric(df["workload_text"].str.extract(r"(\d+)")[0]).astype("Int64"),
        has_certificate=df["certificate_text"].notna(),
        price_rub=parse_price(df["price_text"]),
        old_price_rub=parse_price(df["old_price_text"]),
        courses_included=pd.to_numeric(df["badge_text"].str.extract(r"(\d+)")[0]).astype("Int64"),
        course_url=config.BASE_URL + "/course/" + df["course_id"].astype(str),
        snapshot_date=scraped_at.dt.strftime("%Y-%m-%d"),
    )
    df["is_free"] = df["price_rub"].eq(0)
    df["discount_pct"] = ((df["old_price_rub"] - df["price_rub"]) / df["old_price_rub"] * 100).round(1)
    return df


def handle_missing(df: pd.DataFrame) -> pd.DataFrame:
    """Fill only gaps whose meaning is known; an unknown rating or workload stays NULL instead of being invented."""
    fill = {"reviews_count": 0, "learners_count": 0, "discount_pct": 0.0, "courses_included": 1, "authors": "Unknown"}
    before = df[COLUMNS].isna().sum()
    df = df.fillna(fill)
    after = df[COLUMNS].isna().sum()
    report = pd.DataFrame({"before": before, "after": after}).query("before > 0")
    log.info("Missing values per column:\n%s", report.to_string())
    return df


def drop_invalid(df: pd.DataFrame) -> pd.DataFrame:
    rules = {
        "rating outside 1-5": df["rating"].notna() & ~df["rating"].between(1, 5),
        "negative counts": (df["reviews_count"] < 0) | (df["learners_count"] < 0),
        "non-positive workload": df["workload_hours"].notna() & (df["workload_hours"] <= 0),
        "unknown price": df["price_rub"].isna() | (df["price_rub"] < 0),
        "old price not above price": df["old_price_rub"].notna() & (df["old_price_rub"] <= df["price_rub"]),
        "unknown difficulty": df["difficulty"].isna(),
    }
    invalid = pd.concat(rules, axis=1).fillna(False)
    for rule, mask in invalid.items():
        if mask.any():
            log.warning("Rule '%s' failed for %d records", rule, int(mask.sum()))
    log.info("Invalid records removed: %d", int(invalid.any(axis=1).sum()))
    return df[~invalid.any(axis=1)]


def clean_courses(raw: pd.DataFrame) -> pd.DataFrame:
    log.info("Raw records: %d", len(raw))
    df = drop_empty_cards(raw)
    df = remove_duplicates(df)
    df = normalize_text_fields(df)
    df = convert_types(df)
    df = handle_missing(df)
    df = drop_invalid(df)
    clean = df[COLUMNS].sort_values(["difficulty", "catalog_rank"]).reset_index(drop=True)
    log.info("Clean records: %d (%s)", len(clean), clean["difficulty"].value_counts().to_dict())
    return clean


def latest(folder: Path, pattern: str) -> Path:
    files = sorted(folder.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No {pattern} files in {folder}")
    return files[-1]


def run(raw_path: Path | None = None, out_dir: Path = config.CLEAN_DIR) -> Path:
    raw_path = raw_path or latest(config.RAW_DIR, "courses_raw_*.csv")
    clean = clean_courses(pd.read_csv(raw_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / raw_path.name.replace("_raw_", "_clean_")
    clean.to_csv(out_path, index=False)
    log.info("Saved %s", out_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_csv", nargs="?", type=Path, help="raw file (default: the newest in data/raw)")
    args = parser.parse_args()
    config.setup_logging()
    run(args.raw_csv)


if __name__ == "__main__":
    main()
