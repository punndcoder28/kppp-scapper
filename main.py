"""
Karnataka Tender Scraper — entry point.

Usage:
    python main.py                        # run for today (email primary)
    python main.py --date 2024-06-01      # run for a specific date
    python main.py --since 2024-06-01     # fetch emails since this date
    python main.py --dry-run              # scrape only, skip Claude evaluation
    python main.py --no-email             # skip email, go straight to web scraper
"""

import argparse
import asyncio
import logging
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Default lookback: 4 days covers Mon→Thu and Thu→Mon windows with a buffer
DEFAULT_LOOKBACK_DAYS = 4


async def run(run_date: date, since_date: date, dry_run: bool, no_email: bool) -> int:
    from scraper.browser import scrape_primary, scrape_fallback, ScrapingError
    from scraper.evaluator import evaluate_tenders
    from scraper.reporter import (
        generate_report,
        generate_error_report,
        generate_empty_report,
    )

    tenders = None
    source = "kppp"

    # ── Step 1: Try email first ─────────────────────────────────────────────
    if not no_email:
        try:
            from scraper.mail import fetch_tenders_from_email
            log.info(f"Fetching KPPP emails since {since_date}…")
            tenders = fetch_tenders_from_email(since_date)
            source = "email"
            log.info(f"Email source: {len(tenders)} tenders from PDFs")
        except Exception as e:
            log.warning(f"Email fetch failed: {e} — falling back to web scraper")
            tenders = None

    # ── Step 2: Web scraper fallback ────────────────────────────────────────
    if tenders is None:
        primary_error = ""
        fallback_error = ""
        try:
            tenders = await scrape_primary()
            source = "kppp"
        except ScrapingError as e:
            primary_error = str(e)
            log.warning(f"Primary scrape failed: {e}")
            log.info("Trying eproc fallback…")
            try:
                tenders = await scrape_fallback()
                source = "eproc"
            except ScrapingError as e2:
                fallback_error = str(e2)
                log.error(f"Fallback scrape also failed: {e2}")

        if tenders is None:
            log.error("All sources failed — writing error report")
            path = generate_error_report(run_date, primary_error, fallback_error)
            log.info(f"Error report: {path}")
            return 1

    if not tenders:
        log.info("No tenders found — writing empty report")
        path = generate_empty_report(run_date, source)
        log.info(f"Empty report: {path}")
        return 0

    log.info(f"Total tenders: {len(tenders)} (source: {source})")

    # ── Step 3: Evaluate ────────────────────────────────────────────────────
    if dry_run:
        log.info("--dry-run: skipping Claude evaluation")
        evaluations = [{"label": "other_supply", "reason": "dry run"} for _ in tenders]
    else:
        evaluations = await evaluate_tenders(tenders)

    label_counts: dict[str, int] = {}
    for ev in evaluations:
        label_counts[ev["label"]] = label_counts.get(ev["label"], 0) + 1
    log.info(f"Evaluation results: {label_counts}")

    # ── Step 4: Report ──────────────────────────────────────────────────────
    path = generate_report(run_date, tenders, evaluations, source=source)
    log.info(f"Report written: {path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Karnataka tender scraper")
    parser.add_argument("--date", help="Run date (YYYY-MM-DD), defaults to today")
    parser.add_argument(
        "--since",
        help="Fetch emails since this date (YYYY-MM-DD), defaults to 4 days ago",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip Claude evaluation")
    parser.add_argument("--no-email", action="store_true", help="Skip email, use web scraper only")
    args = parser.parse_args()

    run_date = date.fromisoformat(args.date) if args.date else date.today()
    since_date = (
        date.fromisoformat(args.since)
        if args.since
        else run_date - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    )

    log.info(f"Run date: {run_date}, fetching emails since: {since_date}")
    exit_code = asyncio.run(run(run_date, since_date, args.dry_run, args.no_email))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
