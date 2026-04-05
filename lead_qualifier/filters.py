"""Gate 1: Hard filters on revenue and employees."""

from typing import Dict, List, Tuple

from .models import Company


def apply_hard_filters(companies: List[Company], config: Dict) -> Tuple[List[Company], List[Company], List[Company]]:
    """Apply Gate 1 hard filters.

    Returns (passed, rejected, manual_review).
    """
    min_revenue = config["min_revenue"]
    min_employees = config["min_employees"]

    passed = []
    rejected = []
    manual_review = []

    for company in companies:
        # No website = reject (can't scrape or detect platforms)
        if not company.domain:
            company.status = "REJECTED"
            company.rejection_reason = "No website/domain"
            company.gate_reached = 1
            rejected.append(company)
            continue

        has_revenue = company.revenue is not None
        has_employees = company.employees is not None

        # If both are missing, manual review
        if not has_revenue and not has_employees:
            company.status = "MANUAL_REVIEW"
            company.rejection_reason = "Missing revenue and employee data"
            company.gate_reached = 1
            manual_review.append(company)
            continue

        failed = False

        # Check revenue if available
        if has_revenue and company.revenue < min_revenue:
            company.status = "REJECTED"
            company.rejection_reason = f"Revenue ${company.revenue:,.0f} below minimum ${min_revenue:,.0f}"
            company.gate_reached = 1
            rejected.append(company)
            failed = True

        # Check employees if available
        if not failed and has_employees and company.employees < min_employees:
            company.status = "REJECTED"
            company.rejection_reason = f"Employees {company.employees} below minimum {min_employees}"
            company.gate_reached = 1
            rejected.append(company)
            failed = True

        if not failed:
            # If one metric is missing but the other passes, still pass (but note it)
            if not has_revenue:
                company.rejection_reason = "Missing revenue data (passed on employees)"
            elif not has_employees:
                company.rejection_reason = "Missing employee data (passed on revenue)"
            company.gate_reached = 1
            passed.append(company)

    return passed, rejected, manual_review
