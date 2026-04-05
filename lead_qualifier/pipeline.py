"""Main pipeline orchestration — runs the full qualification flow."""

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from .classifier import apply_service_filter, classify_batch, classify_realtime, estimate_cost
from .config import format_revenue
from .csv_loader import load_csv
from .dashboard import generate_dashboard
from .database import Database
from .excel_writer import generate_excel, generate_export_all_excel
from .filters import apply_hard_filters
from .models import Company
from .platform_detector import detect_platforms_batch
from .review_scraper import filter_by_reviews, scrape_reviews_batch
from .website_scraper import scrape_websites_batch

logger = logging.getLogger(__name__)


def _print(msg: str):
    print(msg, flush=True)


def _progress_bar(current: int, total: int, width: int = 40) -> str:
    pct = current / total if total > 0 else 1.0
    filled = int(width * pct)
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    return f"[{current:,} / {total:,}] {bar} {pct:.0%}"


def run_pipeline(config: Dict[str, Any]):
    """Execute the full lead qualification pipeline."""
    db = Database(config["db"])

    # --- Handle special modes ---
    if config.get("stats"):
        _print(db.get_stats_summary())
        db.close()
        return

    if config.get("dashboard_only"):
        dashboard_path = config.get("dashboard", "dashboard.html")
        generate_dashboard(db, dashboard_path)
        _print(f"\U0001f4c8 Dashboard generated: {dashboard_path}")
        site_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "site")
        if os.path.isdir(site_dir):
            generate_dashboard(db, os.path.join(site_dir, "index.html"))
            _print(f"\U0001f310 Vercel dashboard: site/index.html")
        db.close()
        return

    if config.get("export_all"):
        output = config.get("output", "all_qualified.xlsx")
        companies = db.get_all_qualified()
        generate_export_all_excel(output, companies)
        _print(f"\U0001f4c4 Exported {len(companies)} qualified leads to {output}")
        db.close()
        return

    # --- Normal pipeline run ---
    input_file = config.get("input")
    if not input_file:
        _print("Error: No input file specified. Use -i <file.csv>")
        db.close()
        sys.exit(1)

    output_file = config.get("output", "qualified.xlsx")
    dashboard_path = config.get("dashboard", "dashboard.html")

    # Print settings
    db_count = db.get_company_count()
    run_count = db.get_run_count()
    _print(f"\n\u2699\ufe0f  Filter settings:")
    _print(f"   Min revenue:   {format_revenue(config['min_revenue'])}")
    _print(f"   Min employees: {config['min_employees']}")
    _print(f"   Min reviews:   {config['min_reviews']}")
    _print(f"   Service type:  {config['service_type']}")
    _print(f"   Database:      {config['db']} ({db_count:,} companies from {run_count} previous runs)")

    # Start run record
    run_id = db.start_run(input_file, {
        k: v for k, v in config.items()
        if k in ("min_revenue", "min_employees", "min_reviews", "service_type")
    })

    stats = {
        "total_in_csv": 0, "new_companies": 0, "skipped_duplicates": 0,
        "rechecked": 0, "passed_gate1": 0, "passed_gate2": 0,
        "passed_gate3": 0, "total_qualified": 0, "total_rejected": 0,
        "total_manual_review": 0, "platforms_detected": 0, "estimated_cost": 0.0,
    }

    try:
        # Load CSV
        limit = config.get("limit")
        companies = load_csv(input_file, limit=limit)
        stats["total_in_csv"] = len(companies)
        _print(f"\n\U0001f4ca Loaded {len(companies):,} companies from CSV")

        # --- DEDUP ---
        to_process: List[Company] = []
        skipped = 0
        rechecked = 0
        recheck = config.get("recheck", False)
        recheck_after = config.get("recheck_after")

        for company in companies:
            existing = db.find_existing(company)
            if existing:
                should, reason = db.should_process(
                    existing, recheck=recheck, recheck_after=recheck_after
                )
                if should:
                    company.db_id = existing["id"]
                    company.old_status = existing["status"]
                    # Carry forward cached data
                    if existing.get("website_text") and not config.get("force_scrape"):
                        company.website_text = existing["website_text"]
                    if existing.get("google_review_count") is not None:
                        company.google_review_count = existing["google_review_count"]
                        company.google_rating = existing.get("google_rating")
                    to_process.append(company)
                    rechecked += 1
                else:
                    skipped += 1
            else:
                to_process.append(company)

        stats["skipped_duplicates"] = skipped
        stats["new_companies"] = len(to_process) - rechecked
        stats["rechecked"] = rechecked

        _print(f"   \U0001f504 {skipped:,} already in database (skipped)")
        _print(f"   \U0001f195 {stats['new_companies']:,} new companies to process")
        if rechecked > 0:
            _print(f"   \U0001f503 {rechecked:,} re-checking previously processed")

        if config.get("dry_run"):
            _print(f"\n\U0001f6a7 Dry run — would process {len(to_process):,} companies. Exiting.")
            db.fail_run(run_id, "dry_run")
            db.close()
            return

        if not to_process:
            _print(f"\n\u2705 No new companies to process.")
            db.complete_run(run_id, stats, output_file)
            generate_dashboard(db, dashboard_path)
            _print(f"\U0001f4c8 Dashboard: {dashboard_path}")
            db.close()
            return

        # --- GATE 1: Hard Filters ---
        _print(f"\n\u2500\u2500 Gate 1: Revenue >= {format_revenue(config['min_revenue'])} + Employees >= {config['min_employees']} \u2500\u2500")
        g1_passed, g1_rejected, g1_manual = apply_hard_filters(to_process, config)
        stats["passed_gate1"] = len(g1_passed)
        _print(f"   \u274c {len(g1_rejected):,} rejected")
        if g1_manual:
            _print(f"   \U0001f50d {len(g1_manual):,} missing data (manual review)")
        _print(f"   \u2705 {len(g1_passed):,} pass \u2192 checking Google reviews")

        all_rejected = list(g1_rejected)
        all_manual = list(g1_manual)

        # --- GATE 2: Google Reviews ---
        if config.get("skip_reviews"):
            _print(f"\n\u2500\u2500 Gate 2: Skipped (--skip-reviews) \u2500\u2500")
            g2_passed = g1_passed
            stats["passed_gate2"] = len(g2_passed)
        else:
            _print(f"\n\u2500\u2500 Gate 2: Google Reviews >= {config['min_reviews']} \u2500\u2500")

            def review_progress(current, total, blocked=False):
                if blocked:
                    return
                sys.stdout.write(f"\r   \U0001f50d Scraping Google... {_progress_bar(current, total)}")
                sys.stdout.flush()
                if current == total:
                    sys.stdout.write("\n")

            scrape_reviews_batch(g1_passed, callback=review_progress)

            g2_passed, g2_rejected, g2_manual = filter_by_reviews(g1_passed, config["min_reviews"])
            stats["passed_gate2"] = len(g2_passed)
            _print(f"   \u274c {len(g2_rejected):,} rejected (under {config['min_reviews']} reviews)")
            if g2_manual:
                _print(f"   \U0001f50d {len(g2_manual):,} couldn't find reviews (manual review)")
            _print(f"   \u2705 {len(g2_passed):,} have {config['min_reviews']}+ reviews \u2192 scraping websites")
            all_rejected.extend(g2_rejected)
            all_manual.extend(g2_manual)

        # --- GATE 3: Website + Platform + AI ---
        _print(f"\n\u2500\u2500 Gate 3: Website Scrape + Classification \u2500\u2500")

        # 3a: Scrape websites
        def website_progress(current, total):
            sys.stdout.write(f"\r   \U0001f310 Scraping websites... {_progress_bar(current, total)}")
            sys.stdout.flush()
            if current == total:
                sys.stdout.write("\n")

        force_scrape = config.get("force_scrape", False)
        g2_passed, raw_htmls = scrape_websites_batch(
            g2_passed, force=force_scrape, callback=website_progress
        )

        # 3b: Platform detection
        platform_counts = detect_platforms_batch(g2_passed, raw_htmls)
        platforms_total = sum(1 for c in g2_passed if c.detected_platforms)
        stats["platforms_detected"] = platforms_total

        _print(f"   \U0001f50c Platform detection: {platforms_total} companies have known platforms")
        if platform_counts:
            top_platforms = sorted(platform_counts.items(), key=lambda x: -x[1])[:5]
            parts = [f"{name}: {count}" for name, count in top_platforms]
            _print(f"      {' | '.join(parts)}")

        # 3c: AI Classification
        if config.get("skip_ai") or config["service_type"] == "any":
            _print(f"   \U0001f916 AI classification: Skipped")
            # All pass as qualified
            for company in g2_passed:
                company.status = "QUALIFIED"
                company.service_type = "any"
                company.gate_reached = 3
            g3_qualified = list(g2_passed)
            g3_rejected = []
            g3_manual = []
            stats["passed_gate3"] = len(g3_qualified)
        else:
            cost = estimate_cost(len(g2_passed), realtime=config.get("realtime", False))
            stats["estimated_cost"] = cost

            if config.get("realtime"):
                _print(f"   \U0001f916 Classifying {len(g2_passed):,} companies (realtime)...")

                def ai_progress(current, total):
                    sys.stdout.write(f"\r   \U0001f916 Classifying... {_progress_bar(current, total)}")
                    sys.stdout.flush()
                    if current == total:
                        sys.stdout.write("\n")

                classify_realtime(g2_passed, config["service_type"], callback=ai_progress)
            else:
                _print(f"   \U0001f4e4 Submitting batch of {len(g2_passed):,} to Claude Haiku...")

                def batch_progress(poll_count, status):
                    _print(f"   \u23f3 Batch processing... (poll #{poll_count}, status: {status})")

                classify_batch(g2_passed, config["service_type"], callback=batch_progress)

            g3_qualified, g3_rejected, g3_manual = apply_service_filter(
                g2_passed, config["service_type"]
            )
            stats["passed_gate3"] = len(g3_qualified)

            _print(f"\n   \u274c {len(g3_rejected):,} commercial only")
            if g3_manual:
                _print(f"   \U0001f50d {len(g3_manual):,} unclear (manual review)")
            _print(f"   \u2705 {len(g3_qualified):,} do residential work \u2192 QUALIFIED")

        all_rejected.extend(g3_rejected)
        all_manual.extend(g3_manual)

        stats["total_qualified"] = len(g3_qualified)
        stats["total_rejected"] = len(all_rejected)
        stats["total_manual_review"] = len(all_manual)

        # --- SAVE TO DATABASE ---
        _save_all_companies(db, run_id, g3_qualified, all_rejected, all_manual)

        # --- GENERATE OUTPUT ---
        qualified_dicts = _companies_to_dicts(g3_qualified)
        manual_dicts = _companies_to_dicts(all_manual)
        rejected_dicts = _companies_to_dicts(all_rejected)

        generate_excel(output_file, qualified_dicts, manual_dicts, rejected_dicts,
                       stats, config, platform_counts)

        db.complete_run(run_id, stats, output_file)

        generate_dashboard(db, dashboard_path)

        # Also generate to site/index.html for Vercel hosting
        site_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "site")
        if os.path.isdir(site_dir):
            site_dashboard = os.path.join(site_dir, "index.html")
            generate_dashboard(db, site_dashboard)
            _print(f"   \U0001f310 Vercel dashboard: site/index.html")

        # --- SUMMARY ---
        _print(f"\n\u2500\u2500 Run #{run_id} Summary \u2500\u2500")
        _print(f"   \u2705 New qualified:    {len(g3_qualified):,}  ({platforms_total} with detected platforms)")
        _print(f"   \U0001f50d Manual review:   {len(all_manual):,}")
        _print(f"   \u274c Rejected:         {len(all_rejected):,}")
        _print(f"   \U0001f504 Skipped (dupes): {skipped:,}")
        _print(f"   \U0001f4b0 API cost:        ${stats['estimated_cost']:.2f}")
        _print(f"")
        db_total = db.get_company_count()
        db_qualified = len(db.get_all_qualified())
        _print(f"   \U0001f4ca Database totals: {db_total:,} companies | {db_qualified:,} qualified all-time")
        _print(f"   \U0001f4c4 Excel: {output_file}")
        _print(f"   \U0001f4c8 Dashboard: {dashboard_path}")

    except KeyboardInterrupt:
        _print("\n\u26a0\ufe0f  Interrupted! Saving progress...")
        db.fail_run(run_id, "interrupted")
    except Exception as e:
        logger.exception(f"Pipeline failed: {e}")
        _print(f"\n\u274c Pipeline failed: {e}")
        db.fail_run(run_id, str(e))
    finally:
        db.close()


def _save_all_companies(db: Database, run_id: int,
                        qualified: List[Company], rejected: List[Company],
                        manual: List[Company]):
    """Save all processed companies to the database."""
    for company_list in [qualified, rejected, manual]:
        for company in company_list:
            old_status = company.old_status
            new_status = company.status

            if company.db_id:
                # Update existing
                db.update_company(company.db_id, company, run_id)
                if old_status != new_status:
                    details = ""
                    if old_status == "REJECTED" and new_status == "QUALIFIED":
                        details = f"Re-qualified (was {old_status})"
                    db.record_status_change(
                        company.db_id, run_id, old_status, new_status,
                        company.gate_reached, details
                    )
            else:
                # Insert new
                try:
                    company_id = db.insert_company(company, run_id)
                    company.db_id = company_id
                    db.record_status_change(
                        company_id, run_id, None, new_status,
                        company.gate_reached, "New company"
                    )
                except Exception as e:
                    logger.warning(f"Failed to insert {company.name}: {e}")


def _companies_to_dicts(companies: List[Company]) -> List[Dict]:
    """Convert Company objects to dicts for Excel output."""
    return [
        {
            "name": c.name,
            "domain": c.domain,
            "city": c.city,
            "state": c.state,
            "phone": c.phone,
            "revenue": c.revenue,
            "employees": c.employees,
            "google_review_count": c.google_review_count,
            "google_rating": c.google_rating,
            "detected_platforms": c.detected_platforms,
            "service_type": c.service_type,
            "ai_reasoning": c.ai_reasoning,
            "status": c.status,
            "rejection_reason": c.rejection_reason,
        }
        for c in companies
    ]
