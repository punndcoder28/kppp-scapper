"""
Karnataka tender scraper.

Primary:  Direct HTTP calls to the KPPP public REST API (no browser needed).
          Endpoint: https://kppp.karnataka.gov.in/supplier-registration-service/v1/api
          Discovered by reverse-engineering main.js — no auth required.

Fallback: Playwright headless Chromium on the legacy eproc JSF portal.
          https://eproc.karnataka.gov.in/eprocurement/common/eproc_tenders_list.seam
"""

import asyncio
import logging
import math
import os
import re

import httpx
from playwright.async_api import async_playwright, Page

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# KPPP direct API constants
# ---------------------------------------------------------------------------
KPPP_BASE = (
    "https://kppp.karnataka.gov.in"
    "/supplier-registration-service/v1/api/portal-service"
)
ENDPOINTS = {
    "GOODS":    f"{KPPP_BASE}/search-eproc-tenders",
    "WORKS":    f"{KPPP_BASE}/works/search-eproc-tenders",
    "SERVICES": f"{KPPP_BASE}/services/search-eproc-tenders",
}
PAGE_SIZE = 50
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://kppp.karnataka.gov.in",
    "Referer": "https://kppp.karnataka.gov.in/",
}

EPROC_URL = (
    "https://eproc.karnataka.gov.in"
    "/eprocurement/common/eproc_tenders_list.seam"
)
HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"


class ScrapingError(Exception):
    pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def scrape_primary() -> list[dict]:
    """Fetch all live tenders from KPPP via direct REST API calls."""
    log.info("Starting primary scrape (KPPP direct API)")
    try:
        tenders = await _scrape_kppp_api()
        log.info(f"Primary scrape complete: {len(tenders)} tenders")
        return tenders
    except Exception as e:
        raise ScrapingError(f"Primary scrape failed: {e}") from e


async def scrape_fallback() -> list[dict]:
    """Scrape eproc.karnataka.gov.in via Playwright / JSF form interaction."""
    log.info("Starting fallback scrape (eproc Playwright)")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context()
        try:
            tenders = await _run_fallback(context)
            log.info(f"Fallback scrape complete: {len(tenders)} tenders")
            return tenders
        except Exception as e:
            raise ScrapingError(f"Fallback scrape failed: {e}") from e
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Primary: KPPP direct HTTP
# ---------------------------------------------------------------------------

async def _scrape_kppp_api() -> list[dict]:
    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=30.0) as client:
        tasks = [
            _fetch_category(client, category, endpoint)
            for category, endpoint in ENDPOINTS.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_tenders: list[dict] = []
    for category, result in zip(ENDPOINTS.keys(), results):
        if isinstance(result, Exception):
            log.warning(f"Category {category} failed: {result}")
        else:
            all_tenders.extend(result)
            log.info(f"Category {category}: {len(result)} tenders")

    return _deduplicate(all_tenders, "tender_number")


async def _fetch_category(client: httpx.AsyncClient, category: str, endpoint: str) -> list[dict]:
    """Fetch all pages for a single tender category."""
    body = _search_body(category)
    url = f"{endpoint}?page=0&size={PAGE_SIZE}&order-by-tender-publish=true"

    response = await client.post(url, json=body)
    response.raise_for_status()

    tenders = [_normalize_kppp(t) for t in response.json() if isinstance(t, dict)]
    total = int(response.headers.get("X-Total-Count", len(tenders)))
    total_pages = math.ceil(total / PAGE_SIZE)

    log.debug(f"{category}: page 1/{total_pages}, total={total}")

    if total_pages > 1:
        remaining_tasks = [
            _fetch_page(client, endpoint, body, page)
            for page in range(1, total_pages)
        ]
        pages = await asyncio.gather(*remaining_tasks, return_exceptions=True)
        for i, page_result in enumerate(pages):
            if isinstance(page_result, Exception):
                log.warning(f"{category} page {i + 2} failed: {page_result}")
            else:
                tenders.extend(page_result)

    return tenders


async def _fetch_page(client: httpx.AsyncClient, endpoint: str, body: dict, page: int) -> list[dict]:
    url = f"{endpoint}?page={page}&size={PAGE_SIZE}&order-by-tender-publish=true"
    response = await client.post(url, json=body)
    response.raise_for_status()
    return [_normalize_kppp(t) for t in response.json() if isinstance(t, dict)]


def _search_body(category: str) -> dict:
    return {
        "tenderNumber": None,
        "category": category,
        "status": "PUBLISHED",
        "deptId": None,
        "publishedFromDate": None,
        "publishedToDate": None,
        "tenderType": "OPEN",
        "title": None,
        "ecv": None,
        "location": None,
        "tenderClosureFromDate": None,
        "tenderClosureToDate": None,
    }


# ---------------------------------------------------------------------------
# Fallback: eproc Playwright
# ---------------------------------------------------------------------------

async def _run_fallback(context) -> list[dict]:
    all_tenders = []
    for category in ["GOODS", "WORKS", "SERVICES"]:
        page = await context.new_page()
        page.set_default_timeout(45_000)
        try:
            tenders = await _scrape_eproc_category(page, category)
            all_tenders.extend(tenders)
            log.info(f"eproc {category}: {len(tenders)} tenders")
        except Exception as e:
            log.warning(f"eproc category {category} failed: {e}")
        finally:
            await page.close()

    return _deduplicate(all_tenders, "tender_number")


async def _scrape_eproc_category(page: Page, category: str) -> list[dict]:
    await page.goto(EPROC_URL, wait_until="networkidle")

    # Set status to PUBLISHED and select category
    await page.select_option("select#eprocTenders\\:status", "PUBLISHED")
    if category != "GOODS":
        await page.select_option("select#eprocTenders\\:tenderCategory", category)

    # Submit the search
    await page.click("input#eprocTenders\\:butSearch")
    await page.wait_for_load_state("networkidle", timeout=20_000)
    await asyncio.sleep(1)

    tenders = []
    page_num = 1
    while True:
        rows = await _parse_eproc_results_table(page)
        tenders.extend(rows)
        log.debug(f"eproc {category} page {page_num}: {len(rows)} rows")

        # Look for a Next page link
        next_btn = await page.query_selector(
            "a[id*='next']:not([class*='disabled']), "
            "a[title='Next']:not([class*='disabled'])"
        )
        if not next_btn:
            break
        await next_btn.click()
        await page.wait_for_load_state("networkidle", timeout=20_000)
        page_num += 1
        if page_num > 200:  # safety cap
            break

    return tenders


async def _parse_eproc_results_table(page: Page) -> list[dict]:
    """Parse the tender results table from eproc.

    The results table has id='eprocTenders:tenderList' or similar.
    We look for the table that contains actual tender records (rows with
    tender numbers), not the search form tables.
    """
    rows = await page.evaluate("""() => {
        const results = [];
        // Find tables that look like result tables (have links to tender details)
        const tables = document.querySelectorAll('table');
        for (const table of tables) {
            const rows = Array.from(table.querySelectorAll('tr'));
            // Skip tables with very few rows or that look like form/nav tables
            if (rows.length < 2) continue;
            for (let i = 1; i < rows.length; i++) {
                const cells = Array.from(rows[i].querySelectorAll('td'));
                if (cells.length < 4) continue;
                // A valid tender row should have a link with a tender number pattern
                const links = rows[i].querySelectorAll('a[href*="tender"], a[id*="tender"]');
                const allText = cells.map(c => c.innerText.trim());
                // Filter out rows that are clearly form elements
                const joined = allText.join('|');
                if (joined.includes('Select') && joined.includes('Goods') && joined.includes('Works')) continue;
                if (allText[0].match(/\\d/) || allText[1].match(/\\d/) || links.length > 0) {
                    const href = links.length > 0 ? links[0].href : '';
                    results.push({ cells: allText, href });
                }
            }
        }
        return results;
    }""")

    tenders = []
    for row in rows:
        tender = _normalize_eproc(row["cells"], row.get("href", ""))
        if tender.get("tender_number") or tender.get("title"):
            tenders.append(tender)
    return tenders


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_kppp(raw: dict) -> dict:
    """Normalize a KPPP API response object to canonical schema."""
    nit_url = ""
    nit_id = raw.get("nitId")
    if nit_id:
        nit_url = (
            f"https://kppp.karnataka.gov.in/#/portal/tender-view/"
            f"{nit_id}/{raw.get('category', 'GOODS').lower()}"
        )

    return {
        "tender_number": _str(raw.get("tenderNumber")),
        "title": _str(raw.get("title")),
        "description": _str(raw.get("description")),
        "department": _str(raw.get("deptName")),
        "district": _str(raw.get("locationName")),
        "category": _str(raw.get("categoryText") or raw.get("category")),
        "sub_category": "",
        "ecv": _parse_ecv(raw.get("ecv")),
        "published_date": _str(raw.get("publishedDate")),
        "deadline": _str(raw.get("tenderClosureDate")),
        "status": _str(raw.get("statusText") or raw.get("status")),
        "nit_url": nit_url,
        "source": "kppp",
    }


def _normalize_eproc(cells: list[str], href: str) -> dict:
    """Best-effort normalization from eproc HTML table cells.

    Typical eproc column order:
    [0] S.No  [1] Dept  [2] Location  [3] Tender No  [4] Title
    [5] Type  [6] ECV   [7] Published [8] Deadline   [9] Actions
    """
    n = len(cells)
    # Try to identify tender number column (looks like DEPT/YEAR-YY/XXXNNNNN)
    tender_no = ""
    title = ""
    for i, cell in enumerate(cells):
        if re.match(r"[A-Z]+/\d{4}", cell):
            tender_no = cell
            title = cells[i + 1] if i + 1 < n else ""
            break

    return {
        "tender_number": tender_no or (cells[3] if n > 3 else ""),
        "title": title or (cells[4] if n > 4 else ""),
        "description": "",
        "department": cells[1] if n > 1 else "",
        "district": cells[2] if n > 2 else "",
        "category": "",
        "sub_category": "",
        "ecv": _parse_ecv(cells[6] if n > 6 else None),
        "published_date": cells[7] if n > 7 else "",
        "deadline": cells[8] if n > 8 else "",
        "status": "Published",
        "nit_url": href,
        "source": "eproc",
    }


def _parse_ecv(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    s = str(raw).strip()
    if not s or s.lower() in ("n/a", "nil", "-", "", "false", "true"):
        return None
    s = re.sub(r"[₹,\s]", "", s)
    s_lower = s.lower()
    try:
        if "crore" in s_lower:
            return float(re.sub(r"[^0-9.]", "", s_lower.replace("crores", "").replace("crore", ""))) * 1e7
        if "lakh" in s_lower:
            return float(re.sub(r"[^0-9.]", "", s_lower.replace("lakhs", "").replace("lakh", ""))) * 1e5
        val = float(re.sub(r"[^0-9.]", "", s))
        return val if val > 0 else None
    except ValueError:
        return None


def _str(val) -> str:
    return str(val).strip() if val is not None else ""


def _deduplicate(tenders: list[dict], key: str) -> list[dict]:
    seen: set[str] = set()
    result = []
    for t in tenders:
        k = t.get(key, "")
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        result.append(t)
    return result
