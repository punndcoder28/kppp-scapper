"""
HTML report generator.

Produces reports/YYYY-MM-DD.html from a list of tenders + evaluations.
"""

import logging
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

log = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent.parent / "reports"
TEMPLATES_DIR = Path(__file__).parent / "templates"

LABEL_ORDER = ["lab_equipment", "construction", "other_supply", "low_priority"]


def generate_report(
    run_date: date,
    tenders: list[dict],
    evaluations: list[dict],
    source: str = "unknown",
) -> Path:
    """Render the HTML report and write it to reports/YYYY-MM-DD.html."""
    REPORTS_DIR.mkdir(exist_ok=True)

    partitioned = _partition_tenders(tenders, evaluations)
    counts = {label: len(partitioned[label]) for label in LABEL_ORDER}
    relevant_count = counts["lab_equipment"] + counts["construction"]

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("report.html.j2")

    html = template.render(
        run_date=run_date.isoformat(),
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        total=len(tenders),
        relevant_count=relevant_count,
        counts=counts,
        source=source,
        sections=partitioned,
        format_ecv=format_ecv,
    )

    out_path = REPORTS_DIR / f"{run_date.isoformat()}.html"
    out_path.write_text(html, encoding="utf-8")
    log.info(f"Report written to {out_path}")

    generate_index()
    return out_path


def generate_index() -> Path:
    """Regenerate index.html at the repo root listing all reports."""
    REPORTS_DIR.mkdir(exist_ok=True)
    report_files = sorted(REPORTS_DIR.glob("*.html"), reverse=True)

    entries = []
    for f in report_files:
        report_date = f.stem  # e.g. "2026-04-26"
        entries.append({"date": report_date, "path": f"reports/{f.name}"})

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("index.html.j2")
    html = template.render(
        entries=entries,
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )

    index_path = Path(__file__).parent.parent / "index.html"
    index_path.write_text(html, encoding="utf-8")
    log.info(f"Index written to {index_path}")
    return index_path


def generate_error_report(run_date: date, primary_error: str, fallback_error: str) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    out_path = REPORTS_DIR / f"{run_date.isoformat()}.html"
    out_path.write_text(
        f"""<!DOCTYPE html><html><head><title>Tender Report Error {run_date}</title></head>
<body style="font-family:sans-serif;padding:2rem">
<h1>Tender Scraper Failed — {run_date}</h1>
<p><strong>Primary scraper error:</strong> {primary_error}</p>
<p><strong>Fallback scraper error:</strong> {fallback_error}</p>
<p>Please check the portal manually: <a href="https://kppp.karnataka.gov.in/#/portal/searchTender/live">KPPP Live Tenders</a></p>
</body></html>""",
        encoding="utf-8",
    )
    return out_path


def generate_empty_report(run_date: date, source: str) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    out_path = REPORTS_DIR / f"{run_date.isoformat()}.html"
    out_path.write_text(
        f"""<!DOCTYPE html><html><head><title>Tender Report {run_date}</title></head>
<body style="font-family:sans-serif;padding:2rem">
<h1>Karnataka Tender Report — {run_date}</h1>
<p>No live tenders found today (source: {source}).</p>
</body></html>""",
        encoding="utf-8",
    )
    return out_path


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _partition_tenders(tenders: list[dict], evaluations: list[dict]) -> dict[str, list[dict]]:
    """Split tenders by label, sorted ECV-descending within each group."""
    buckets: dict[str, list[dict]] = {label: [] for label in LABEL_ORDER}

    for tender, ev in zip(tenders, evaluations):
        label = ev.get("label", "other_supply")
        if label not in buckets:
            continue  # skip "skip" tenders
        enriched = {**tender, "_label": label, "_reason": ev.get("reason", "")}
        buckets[label].append(enriched)

    for label in LABEL_ORDER:
        buckets[label].sort(
            key=lambda t: (t.get("ecv") is None, -(t.get("ecv") or 0))
        )

    return buckets


def format_ecv(ecv) -> str:
    if ecv is None or ecv == 0:
        return "N/A"
    ecv = float(ecv)
    if ecv >= 1e7:
        return f"₹{ecv / 1e7:.2f} Crores"
    if ecv >= 1e5:
        return f"₹{ecv / 1e5:.2f} Lakhs"
    return f"₹{ecv:,.0f}"
