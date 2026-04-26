"""
Karnataka Tender Scraper — entry point.

Usage:
    python main.py                  # run for today
    python main.py --date 2024-06-01  # run for a specific date
    python main.py --dry-run        # scrape only, skip Claude evaluation
"""

import argparse
import asyncio
import logging
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


async def run(run_date: date, dry_run: bool) -> int:
    from scraper.browser import scrape_primary, scrape_fallback, ScrapingError
    from scraper.evaluator import evaluate_tenders
    from scraper.reporter import (
        generate_report,
        generate_error_report,
        generate_empty_report,
    )

    # ── Step 1: Scrape ──────────────────────────────────────────────────────
    tenders = None
    source = "kppp"
    primary_error = ""
    fallback_error = ""

    try:
        tenders = await scrape_primary()
        source = "kppp"
    except ScrapingError as e:
        primary_error = str(e)
        log.warning(f"Primary scrape failed: {e}")
        log.info("Trying fallback scraper (eproc)…")
        try:
            tenders = await scrape_fallback()
            source = "eproc"
        except ScrapingError as e2:
            fallback_error = str(e2)
            log.error(f"Fallback scrape also failed: {e2}")

    if tenders is None:
        log.error("Both scrapers failed — writing error report")
        path = generate_error_report(run_date, primary_error, fallback_error)
        log.info(f"Error report: {path}")
        return 1

    if not tenders:
        log.info("No live tenders found — writing empty report")
        path = generate_empty_report(run_date, source)
        log.info(f"Empty report: {path}")
        return 0

    log.info(f"Scraped {len(tenders)} tenders from {source}")

    # ── Step 2: Evaluate ────────────────────────────────────────────────────
    if dry_run:
        log.info("--dry-run: skipping Claude evaluation")
        evaluations = [{"label": "other_supply", "reason": "dry run"} for _ in tenders]
    else:
        evaluations = await evaluate_tenders(tenders)

    label_counts: dict[str, int] = {}
    for ev in evaluations:
        label_counts[ev["label"]] = label_counts.get(ev["label"], 0) + 1
    log.info(f"Evaluation results: {label_counts}")

    # ── Step 3: Report ──────────────────────────────────────────────────────
    path = generate_report(run_date, tenders, evaluations, source=source)
    log.info(f"Report written: {path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Karnataka tender scraper")
    parser.add_argument("--date", help="Run date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--dry-run", action="store_true", help="Skip Claude evaluation")
    args = parser.parse_args()

    if args.date:
        run_date = date.fromisoformat(args.date)
    else:
        run_date = date.today()

    log.info(f"Starting tender scraper for {run_date}")
    exit_code = asyncio.run(run(run_date, args.dry_run))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
