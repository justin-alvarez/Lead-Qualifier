"""HTML dashboard generation with embedded Chart.js."""

import json
import os
from typing import Any, Dict

from .database import Database

DASHBOARD_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "templates", "dashboard_template.html"
)


def gather_dashboard_data(db: Database) -> Dict[str, Any]:
    """Query the database for all dashboard data."""
    return {
        "latest_run": db.query_latest_run(),
        "run_history": db.query_all_runs(),
        "cumulative_stats": db.query_cumulative_stats(),
        "funnel_data": db.query_funnel_data(),
        "platform_breakdown": db.query_platform_breakdown(),
        "review_distribution": db.query_review_distribution(),
        "status_changes": db.query_status_changes(),
        "qualified_leads": db.get_all_qualified(),
    }


def generate_dashboard(db: Database, output_path: str):
    """Generate a self-contained HTML dashboard file."""
    data = gather_dashboard_data(db)

    # Read template
    with open(DASHBOARD_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    # Embed data as JSON
    html = template.replace("{{DATA}}", json.dumps(data, default=str))

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
