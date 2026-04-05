"""CSV parsing with flexible column mapping for ZoomInfo exports."""

import csv
import re
from typing import Dict, List, Optional

from .models import Company


# Maps our internal field names to possible CSV header variations
COLUMN_MAP = {
    "name": ["Company Name", "company_name", "Company", "name"],
    "domain": ["Domain", "Website", "website", "domain", "Company Domain"],
    "revenue": ["Revenue--Clean", "Revenue", "Revenue (in 000s $)", "Annual Revenue", "Revenue Range", "Estimated Revenue"],
    "employees": ["Employees", "Number of Employees", "employees", "Employee Count"],
    "city": ["City", "city", "Company City"],
    "state": ["State", "state", "State/Province", "Company State"],
    "phone": ["Phone", "phone", "Company Phone"],
    "zoominfo_id": ["Company ID", "ZoomInfo Company ID", "zoominfo_id"],
}


def _match_column(header: str, candidates: List[str]) -> bool:
    """Check if a CSV header matches any candidate (case-insensitive)."""
    h = header.strip().lower()
    return any(c.lower() == h for c in candidates)


def _build_header_map(headers: List[str]) -> Dict[str, str]:
    """Map internal field names to actual CSV column names."""
    mapping = {}
    for field, candidates in COLUMN_MAP.items():
        for header in headers:
            if _match_column(header, candidates):
                mapping[field] = header
                break
    return mapping


def parse_revenue(value: str) -> Optional[float]:
    """Parse various revenue formats from CSV.

    Handles: "$2.5M", "$2,500,000", "$1M - $5M" (lower bound),
             "2500" (in 000s), "$1B", etc.
    """
    if not value or not value.strip():
        return None

    s = value.strip()

    # Handle range: take lower bound
    if " - " in s:
        s = s.split(" - ")[0].strip()
    if " to " in s.lower():
        s = s.lower().split(" to ")[0].strip()

    s = s.replace("$", "").replace(",", "").strip()

    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}

    match = re.match(r"^([\d.]+)\s*([KMBkmb])?", s)
    if match:
        num = float(match.group(1))
        suffix = (match.group(2) or "").upper()
        if suffix and suffix in multipliers:
            num *= multipliers[suffix]
        return num

    try:
        return float(s)
    except ValueError:
        return None


def parse_employees(value: str) -> Optional[int]:
    """Parse employee count from CSV."""
    if not value or not value.strip():
        return None

    s = value.strip().replace(",", "")

    # Handle ranges like "10-50": take lower bound
    if "-" in s:
        s = s.split("-")[0].strip()

    try:
        return int(float(s))
    except ValueError:
        return None


def load_csv(filepath: str, limit: Optional[int] = None) -> List[Company]:
    """Load companies from a CSV file.

    Returns a list of Company objects with data from the CSV.
    """
    companies = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV file has no headers: {filepath}")

        header_map = _build_header_map(list(reader.fieldnames))

        if "name" not in header_map:
            raise ValueError(
                f"Cannot find company name column. Headers found: {reader.fieldnames}"
            )

        for i, row in enumerate(reader):
            if limit and i >= limit:
                break

            name = row.get(header_map.get("name", ""), "").strip()
            if not name:
                continue

            revenue_raw = row.get(header_map.get("revenue", ""), "")
            employees_raw = row.get(header_map.get("employees", ""), "")

            company = Company(
                name=name,
                domain=row.get(header_map.get("domain", ""), "").strip() or None,
                zoominfo_id=row.get(header_map.get("zoominfo_id", ""), "").strip() or None,
                city=row.get(header_map.get("city", ""), "").strip() or None,
                state=row.get(header_map.get("state", ""), "").strip() or None,
                phone=row.get(header_map.get("phone", ""), "").strip() or None,
                revenue=parse_revenue(revenue_raw),
                employees=parse_employees(employees_raw),
            )
            companies.append(company)

    return companies
