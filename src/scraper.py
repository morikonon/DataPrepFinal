"""Scrapes the Stepik course catalogue with Selenium.

The catalogue is an Ember.js single-page app: the server sends no courses, the browser first draws empty
placeholder cards, fills a card only when it scrolls into view, and the "Далее" (Next) button re-renders the
list without reloading the page (a ?page=N link is ignored on first load, so pages can only be reached by clicking).
The scraper opens the catalogue once per difficulty level, waits, scrolls and paginates like a user, then parses
the rendered HTML with BeautifulSoup and keeps every value exactly as the site shows it (the cleaner interprets them).
"""

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from src import config

log = logging.getLogger("scraper")

CARD = "div.course-card"
LOADED_TITLE = "a.course-card__title[href]"
NEXT_BUTTON = "//div[contains(@class, 'catalog__paging')]//button[normalize-space()='Далее' and not(@disabled)]"


def make_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if config.HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1400,1000")
    options.add_argument("--lang=ru-RU")
    options.add_experimental_option("prefs", {"intl.accept_languages": "ru-RU,ru"})
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    return driver


def wait(driver: webdriver.Chrome) -> WebDriverWait:
    # Ember replaces card nodes while rendering, so a found element can go stale before it is read.
    return WebDriverWait(driver, config.WAIT_TIMEOUT_S, ignored_exceptions=(StaleElementReferenceException,))


def loaded_urls(driver: webdriver.Chrome) -> list[str]:
    return [a.get_attribute("href") for a in driver.find_elements(By.CSS_SELECTOR, f"{CARD} {LOADED_TITLE}")]


def empty_cards(driver: webdriver.Chrome) -> list:
    return [card for card in driver.find_elements(By.CSS_SELECTOR, CARD)
            if not card.find_elements(By.CSS_SELECTOR, LOADED_TITLE)]


def load_lazy_cards(driver: webdriver.Chrome) -> int:
    """Scroll the page so every placeholder card fetches its content; return how many stayed empty."""
    driver.execute_script("window.scrollTo(0, 0)")
    wait(driver).until(loaded_urls)
    height = driver.execute_script("return document.body.scrollHeight")
    for offset in range(0, height, config.SCROLL_STEP_PX):
        driver.execute_script("window.scrollTo(0, arguments[0])", offset)
        time.sleep(config.SCROLL_PAUSE_S)

    for _ in range(config.FILL_ATTEMPTS):
        try:
            pending = empty_cards(driver)
            if not pending:
                return 0
            for card in pending:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'})", card)
                time.sleep(1)
        except StaleElementReferenceException:
            continue
    return len(empty_cards(driver))


def text_of(node) -> str | None:
    return node.get_text(" ", strip=True) if node else None


def parse_cards(html: str, level: str, page: int, scraped_at: str) -> list[dict]:
    """One raw record per card; placeholders that never loaded are kept with empty fields."""
    records = []
    for position, card in enumerate(BeautifulSoup(html, "lxml").select(CARD), start=1):
        title = card.select_one(LOADED_TITLE)
        widgets = {widget.get("data-type"): widget for widget in card.select(".course-card__widget")}
        records.append({
            "level": level,
            "page": page,
            "position": position,
            "course_url": title.get("href") if title else None,
            "title": text_of(title),
            "authors": " | ".join(text_of(a) for a in card.select(".course-card__author")) or None,
            "summary": text_of(card.select_one(".course-card__summary")),
            "rating_text": text_of(widgets.get("rating")),
            "learners_text": text_of(widgets.get("learners")),
            "workload_text": text_of(widgets.get("workload")),
            "certificate_text": text_of(widgets.get("certificate")),
            "price_text": text_of(card.select_one(".display-price__price_discount, .display-price__price_default")),
            "old_price_text": text_of(card.select_one(".display-price__price_regular")),
            "badge_text": text_of(card.select_one(".course-card__badge")),
            "scraped_at": scraped_at,
        })
    return records


def go_to_next_page(driver: webdriver.Chrome, page: int) -> bool:
    buttons = driver.find_elements(By.XPATH, NEXT_BUTTON)
    if not buttons:
        return False
    previous = set(loaded_urls(driver))
    driver.execute_script("arguments[0].click()", buttons[0])
    wait(driver).until(lambda d: f"page={page + 1}" in d.current_url and previous.isdisjoint(loaded_urls(d)))
    return True


def scrape_level(driver: webdriver.Chrome, level: str, pages: int, scraped_at: str) -> list[dict]:
    driver.get(f"{config.CATALOG_URL}?difficulty={level}")
    records = []
    for page in range(1, pages + 1):
        not_loaded = load_lazy_cards(driver)
        cards = parse_cards(driver.page_source, config.LEVELS[level], page, scraped_at)
        records.extend(cards)
        log.info("Level %-6s page %d: %d cards, %d placeholders did not load", level, page, len(cards), not_loaded)
        if page == pages or not go_to_next_page(driver, page):
            break
    return records


def scrape(pages_per_level: int = config.PAGES_PER_LEVEL, out_dir: Path = config.RAW_DIR) -> Path:
    started = datetime.now(config.TIMEZONE)
    scraped_at = started.isoformat(timespec="seconds")
    records = []
    log.info("Scraping %s: %d levels x %d pages", config.CATALOG_URL, len(config.LEVELS), pages_per_level)
    driver = make_driver()
    try:
        for level in config.LEVELS:
            records.extend(scrape_level(driver, level, pages_per_level, scraped_at))
    finally:
        driver.quit()

    if not records:
        raise RuntimeError("No course cards were found: the page layout may have changed")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"courses_raw_{started:%Y-%m-%d}.csv"
    pd.DataFrame(records).to_csv(path, index=False)
    elapsed = (datetime.now(config.TIMEZONE) - started).total_seconds()
    log.info("Scraped %d cards in %.0f s -> %s", len(records), elapsed, path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pages", type=int, default=config.PAGES_PER_LEVEL,
                        help="pages per difficulty level (20 cards each)")
    args = parser.parse_args()
    config.setup_logging()
    scrape(args.pages)


if __name__ == "__main__":
    main()
