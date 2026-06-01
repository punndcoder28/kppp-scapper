"""
Karnataka Tender Scraper — entry point.

Usage:
    python main.py                        # full run (fetch + evaluate + report)
    python main.py --fetch-only           # fetch tenders, write cache, update last_run.txt
    python main.py --evaluate-only        # evaluate cached tenders, generate report
    python main.py --date 2024-06-01      # run for a specific date
    python main.py --since 2024-06-01     # fetch emails since this date
    python main.py --dry-run              # skip Claude evaluation
    python main.py --no-email             # skip email, use web scraper only
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

LAST_RUN_FILE = Path(__file__).parent / "last_run.txt"
SEEN_TENDERS_FILE = Path(__file__).parent / "seen_tenders.json"
TENDERS_CACHE_FILE = Path(__file__).parent / ".tenders_cache.json"
DEFAULT_LOOKBACK_DAYS = 4


def _read_last_run_date() -> date | None:
    if LAST_RUN_FILE.exists():
        try:
            return date.fromisoformat(LAST_RUN_FILE.read_text().strip())
        except ValueError:
            pass
    return None


def _write_last_run_date(run_date: date) -> None:
    LAST_RUN_FILE.write_text(run_date.isoformat())
    log.info(f"Updated last_run.txt → {run_date}")


def _load_seen_tenders() -> set[str]:
    if SEEN_TENDERS_FILE.exists():
        try:
            return set(json.loads(SEEN_TENDERS_FILE.read_text()))
        except (json.JSONDecodeError, ValueError):
            pass
    return set()


def _save_seen_tenders(seen: set[str]) -> None:
    SEEN_TENDERS_FILE.write_text(json.dumps(sorted(seen), indent=2))
    log.info(f"Updated seen_tenders.json — {len(seen)} total seen")


def _write_tenders_cache(run_date: date, source: str, tenders: list[dict]) -> None:
    TENDERS_CACHE_FILE.write_text(json.dumps(
        {"run_date": run_date.isoformat(), "source": source, "tenders": tenders},
        indent=2
    ))
    log.info(f"Cached {len(tenders)} tenders to {TENDERS_CACHE_FILE.name}")


def _read_tenders_cache() -> tuple[date, str, list[dict]]:
    if not TENDERS_CACHE_FILE.exists():
        raise FileNotFoundError("No tenders cache found — run with --fetch-only first.")
    data = json.loads(TENDERS_CACHE_FILE.read_text())
    return date.fromisoformat(data["run_date"]), data["source"], data["tenders"]


async def fetch_tenders(run_date: date, since_date: date, no_email: bool) -> int:
    """Step 1: fetch tenders, deduplicate, write cache + last_run.txt."""
    from scraper.browser import scrape_primary, scrape_fallback, ScrapingError
    from scraper.reporter import generate_error_report, generate_empty_report

    tenders = None
    source = "kppp"

    if not no_email:
        try:
            from scraper.mail import fetch_tenders_from_email
            log.info(f"Fetching KPPP emails since {since_date}…")
            tenders = fetch_tenders_from_email(since_date)
            source = "email"
            log.info(f"Email source: {len(tenders)} tenders")
        except Exception as e:
            log.warning(f"Email fetch failed: {e} — falling back to web scraper")
            tenders = None

    if tenders is None:
        primary_error = fallback_error = ""
        try:
            tenders = await scrape_primary()
            source = "kppp"
        except ScrapingError as e:
            primary_error = str(e)
            log.warning(f"Primary scrape failed: {e}")
            try:
                tenders = await scrape_fallback()
                source = "eproc"
            except ScrapingError as e2:
                fallback_error = str(e2)
                log.error(f"Fallback scrape also failed: {e2}")

        if tenders is None:
            generate_error_report(run_date, primary_error, fallback_error)
            return 1

    if not tenders:
        generate_empty_report(run_date, source)
        _write_last_run_date(run_date)
        return 0

    seen = _load_seen_tenders()
    before = len(tenders)
    tenders = [t for t in tenders if t.get("tender_number") not in seen]
    skipped = before - len(tenders)
    if skipped:
        log.info(f"Skipped {skipped} already-seen tenders — {len(tenders)} new")

    if not tenders:
        log.info("All tenders already seen — nothing to evaluate")
        _write_last_run_date(run_date)
        return 0

    _write_tenders_cache(run_date, source, tenders)
    _write_last_run_date(run_date)
    log.info(f"Fetch complete: {len(tenders)} new tenders cached")
    return 0


async def evaluate_and_report(dry_run: bool) -> int:
    """Step 2: load cached tenders, evaluate with Claude, generate report."""
    from scraper.evaluator import evaluate_tenders
    from scraper.reporter import generate_report

    run_date, source, tenders = _read_tenders_cache()
    log.info(f"Loaded {len(tenders)} cached tenders for {run_date}")

    if dry_run:
        log.info("--dry-run: skipping Claude evaluation")
        evaluations = [{"label": "other_supply", "reason": "dry run"} for _ in tenders]
    else:
        evaluations = await evaluate_tenders(tenders)

    label_counts: dict[str, int] = {}
    for ev in evaluations:
        label_counts[ev["label"]] = label_counts.get(ev["label"], 0) + 1
    log.info(f"Evaluation results: {label_counts}")

    generate_report(run_date, tenders, evaluations, source=source)

    seen = _load_seen_tenders()
    seen.update(t["tender_number"] for t in tenders if t.get("tender_number"))
    _save_seen_tenders(seen)

    TENDERS_CACHE_FILE.unlink(missing_ok=True)
    return 0


async def run(run_date: date, since_date: date, dry_run: bool, no_email: bool) -> int:
    """Full run: fetch + evaluate + report in one go (local use)."""
    rc = await fetch_tenders(run_date, since_date, no_email)
    if rc != 0:
        return rc
    if not TENDERS_CACHE_FILE.exists():
        return 0  # empty / all-seen, already handled
    return await evaluate_and_report(dry_run)


def main():
    parser = argparse.ArgumentParser(description="Karnataka tender scraper")
    parser.add_argument("--date", help="Run date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--since", help="Fetch emails since this date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Skip Claude evaluation")
    parser.add_argument("--no-email", action="store_true", help="Skip email, use web scraper only")
    parser.add_argument("--fetch-only", action="store_true", help="Fetch tenders and cache, skip evaluation")
    parser.add_argument("--evaluate-only", action="store_true", help="Evaluate cached tenders, skip fetch")
    args = parser.parse_args()

    run_date = date.fromisoformat(args.date) if args.date else date.today()

    if args.evaluate_only:
        log.info("Evaluate-only mode: loading cached tenders")
        exit_code = asyncio.run(evaluate_and_report(args.dry_run))
        sys.exit(exit_code)

    if args.since:
        since_date = date.fromisoformat(args.since)
    elif last := _read_last_run_date():
        since_date = last
        log.info(f"Resuming from last successful run: {since_date}")
    else:
        since_date = run_date - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        log.info(f"No last_run.txt found — defaulting to {DEFAULT_LOOKBACK_DAYS} day lookback")

    log.info(f"Run date: {run_date}, fetching emails since: {since_date}")

    if args.fetch_only:
        exit_code = asyncio.run(fetch_tenders(run_date, since_date, args.no_email))
    else:
        exit_code = asyncio.run(run(run_date, since_date, args.dry_run, args.no_email))

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
