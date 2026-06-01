"""
Fetch the Bill of Quantities (BOQ) for a KPPP tender and save as CSV.

Usage:
    python scripts/get_boq.py <tender_number>
    python scripts/get_boq.py KHB/2026-27/BD/WORK_INDENT757
    python scripts/get_boq.py "KHB/2026-27/BD/WORK_INDENT757" --output ~/Desktop
"""

import argparse
import csv
import sys
from pathlib import Path

import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://kppp.karnataka.gov.in",
    "Referer": "https://kppp.karnataka.gov.in/",
}

BASE = "https://kppp.karnataka.gov.in/supplier-registration-service/v1/api/portal-service"
SEARCH_ENDPOINTS = {
    "WORKS":    f"{BASE}/works/search-eproc-tenders",
    "GOODS":    f"{BASE}/search-eproc-tenders",
    "SERVICES": f"{BASE}/services/search-eproc-tenders",
}
FULL_VIEW_ENDPOINTS = {
    "WORKS":    "works-tender-full-view",
    "GOODS":    "goods-tender-full-view",
    "SERVICES": "service-tender-full-view",
}


def find_tender(client: httpx.Client, tender_number: str) -> dict:
    """Search all categories and return the first matching tender record."""
    for category, endpoint in SEARCH_ENDPOINTS.items():
        body = {
            "tenderNumber": tender_number,
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
        r = client.post(f"{endpoint}?page=0&size=10", json=body)
        r.raise_for_status()
        results = r.json()
        if isinstance(results, list) and results:
            match = next((t for t in results if t.get("tenderNumber") == tender_number), None)
            if match:
                match["_category"] = category
                return match

    # If not found as PUBLISHED, retry without status filter (closed tenders)
    for category, endpoint in SEARCH_ENDPOINTS.items():
        body = {
            "tenderNumber": tender_number,
            "category": category,
            "status": None,
            "deptId": None,
            "publishedFromDate": None,
            "publishedToDate": None,
            "tenderType": None,
            "title": None,
            "ecv": None,
            "location": None,
            "tenderClosureFromDate": None,
            "tenderClosureToDate": None,
        }
        r = client.post(f"{endpoint}?page=0&size=10", json=body)
        if r.status_code == 200:
            results = r.json()
            if isinstance(results, list) and results:
                match = next((t for t in results if t.get("tenderNumber") == tender_number), None)
                if match:
                    match["_category"] = category
                    return match

    raise ValueError(f"Tender '{tender_number}' not found on KPPP portal.")


def fetch_full_view(client: httpx.Client, nit_id: int, category: str) -> dict:
    endpoint = FULL_VIEW_ENDPOINTS.get(category, "works-tender-full-view")
    r = client.get(f"{BASE}/{nit_id}/{endpoint}")
    r.raise_for_status()
    return r.json()


def write_csv(tender: dict, full_view: dict, output_dir: Path) -> Path:
    safe_name = tender["tenderNumber"].replace("/", "_").replace(" ", "_")
    out_path = output_dir / f"{safe_name}_BOQ.csv"

    sub_estimates = full_view.get("tenderSubEstimateList", [])
    indent_items = full_view.get("tenderSchedule", {})

    # GOODS tenders use a different structure
    if not sub_estimates:
        indent_items = full_view.get("indentItemDTOs") or []

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        # Header metadata rows
        writer.writerow(["Tender Number", tender.get("tenderNumber", "")])
        writer.writerow(["Title", tender.get("title", "")])
        writer.writerow(["Department", tender.get("deptName", "")])
        writer.writerow(["Category", tender.get("categoryText", tender.get("category", ""))])
        writer.writerow(["Published Date", tender.get("publishedDate", "")])
        writer.writerow(["Bid Closure", tender.get("tenderClosureDate", "")])
        writer.writerow([])

        if sub_estimates:
            # WORKS tenders
            writer.writerow([
                "Sub Estimate", "Work Category", "Estimate Total (Rs)",
                "Item Code", "Item Category", "Description",
                "Unit", "Quantity", "Base Rate (Rs)", "Final Rate (Rs)", "Net Amount (Rs)"
            ])
            for se in sub_estimates:
                for item in se.get("itemList", []):
                    net = item.get("netAmount")
                    if not net and item.get("baseRate") and item.get("quantity"):
                        net = round(float(item["baseRate"]) * float(item["quantity"]), 2)
                    writer.writerow([
                        se.get("subEstimateName", ""),
                        se.get("workCategoryName", ""),
                        se.get("estimateTotal", ""),
                        item.get("itemCode", ""),
                        item.get("categoryName") or "",
                        item.get("description", ""),
                        item.get("uomName", ""),
                        item.get("quantity", ""),
                        item.get("baseRate", ""),
                        item.get("finalRate", ""),
                        net or "",
                    ])
        elif isinstance(indent_items, list) and indent_items:
            # GOODS / SERVICES tenders
            writer.writerow([
                "Item Code", "Description", "Unit", "Quantity",
                "Base Rate (Rs)", "Net Amount (Rs)"
            ])
            for item in indent_items:
                writer.writerow([
                    item.get("itemCode", ""),
                    item.get("description", ""),
                    item.get("uomName", ""),
                    item.get("quantity", ""),
                    item.get("baseRate", ""),
                    item.get("netAmount", ""),
                ])
        else:
            writer.writerow(["No BOQ line items found for this tender."])

    return out_path


def main():
    parser = argparse.ArgumentParser(description="Fetch KPPP tender BOQ as CSV")
    parser.add_argument("tender_number", help="KPPP tender number e.g. KHB/2026-27/BD/WORK_INDENT757")
    parser.add_argument("--output", default=str(Path.home() / "Desktop"), help="Output directory (default: Desktop)")
    args = parser.parse_args()

    output_dir = Path(args.output).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Searching for tender: {args.tender_number}")

    with httpx.Client(headers=HEADERS, timeout=30) as client:
        tender = find_tender(client, args.tender_number)
        nit_id = tender["nitId"]
        category = tender["_category"]

        print(f"Found: {tender.get('title')}")
        print(f"  NIT ID: {nit_id} | Category: {category} | Dept: {tender.get('deptName')}")

        print("Fetching BOQ...")
        full_view = fetch_full_view(client, nit_id, category)

        sub_estimates = full_view.get("tenderSubEstimateList", [])
        total_items = sum(len(se.get("itemList", [])) for se in sub_estimates)
        print(f"  Sub-estimates: {len(sub_estimates)} | Line items: {total_items}")

        out_path = write_csv(tender, full_view, output_dir)

    print(f"\nCSV saved to: {out_path}")


if __name__ == "__main__":
    main()
