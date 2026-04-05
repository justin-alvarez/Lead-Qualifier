"""CLI argument parsing for lead-qualifier."""

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lead-qualifier",
        description="Qualify HVAC/plumbing/mechanical leads from ZoomInfo CSV exports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m lead_qualifier -i companies.csv -o qualified.xlsx
  python -m lead_qualifier -i companies.csv -o qualified.xlsx --min-revenue 5M --min-reviews 500
  python -m lead_qualifier -i companies.csv -o qualified.xlsx --config config.yaml
  python -m lead_qualifier -i companies.csv -o qualified.xlsx --limit 20
  python -m lead_qualifier -i companies.csv -o qualified.xlsx --recheck
  python -m lead_qualifier -i companies.csv --dry-run
  python -m lead_qualifier --dashboard-only
  python -m lead_qualifier --export-all -o all_qualified.xlsx
  python -m lead_qualifier --stats
        """,
    )

    # Input/Output
    parser.add_argument("-i", "--input", type=str, help="Input CSV file path")
    parser.add_argument("-o", "--output", type=str, default="qualified.xlsx",
                        help="Output Excel file path (default: qualified.xlsx)")
    parser.add_argument("--dashboard", type=str, default="dashboard.html",
                        help="Dashboard HTML output path (default: dashboard.html)")
    parser.add_argument("--config", type=str, help="Path to YAML config file")

    # Filter thresholds
    parser.add_argument("--min-revenue", type=str, dest="min_revenue",
                        help="Minimum revenue (e.g., 2M, 500K, 5000000)")
    parser.add_argument("--min-employees", type=int, dest="min_employees",
                        help="Minimum employee count (default: 5)")
    parser.add_argument("--min-reviews", type=int, dest="min_reviews",
                        help="Minimum Google review count (default: 200)")
    parser.add_argument("--service-type", type=str, dest="service_type",
                        choices=["residential", "commercial", "any"],
                        help="Service type filter (default: residential)")

    # Database
    parser.add_argument("--db", type=str, help="SQLite database path (default: ./lead_qualifier.db)")

    # Processing options
    parser.add_argument("--limit", type=int, help="Only process first N companies from CSV")
    parser.add_argument("--resume", action="store_true", help="Resume an interrupted run")
    parser.add_argument("--recheck", action="store_true",
                        help="Re-check previously rejected companies")
    parser.add_argument("--recheck-after", type=int, dest="recheck_after",
                        help="Re-check rejections older than N days")
    parser.add_argument("--force-scrape", action="store_true", dest="force_scrape",
                        help="Force re-scrape websites (ignore cache)")

    # Skip gates
    parser.add_argument("--skip-reviews", action="store_true", dest="skip_reviews",
                        help="Skip Google review check (Gate 2)")
    parser.add_argument("--skip-ai", action="store_true", dest="skip_ai",
                        help="Skip AI classification (Gate 3c)")

    # API options
    parser.add_argument("--realtime", action="store_true",
                        help="Use realtime API instead of Batch API (2x cost, faster)")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Show what would be processed without running")

    # Special modes
    parser.add_argument("--dashboard-only", action="store_true", dest="dashboard_only",
                        help="Regenerate dashboard from existing database")
    parser.add_argument("--export-all", action="store_true", dest="export_all",
                        help="Export all qualified leads from database")
    parser.add_argument("--stats", action="store_true",
                        help="Show database statistics")

    return parser


def parse_args(argv=None):
    parser = build_parser()
    return parser.parse_args(argv)
