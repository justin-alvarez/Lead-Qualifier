"""SQLite database: schema, queries, dedup logic."""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .models import Company


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zoominfo_id TEXT,
    name TEXT NOT NULL,
    domain TEXT,
    city TEXT,
    state TEXT,
    phone TEXT,
    revenue REAL,
    employees INTEGER,
    google_review_count INTEGER,
    google_rating REAL,
    detected_platforms TEXT,
    website_text TEXT,
    service_type TEXT,
    ai_reasoning TEXT,
    status TEXT NOT NULL,
    rejection_reason TEXT,
    first_seen_run_id INTEGER,
    last_updated_run_id INTEGER,
    first_seen_at TIMESTAMP,
    last_updated_at TIMESTAMP,
    UNIQUE(domain)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status TEXT,
    input_file TEXT,
    settings_json TEXT,
    total_in_csv INTEGER,
    new_companies INTEGER,
    skipped_duplicates INTEGER,
    rechecked INTEGER,
    passed_gate1 INTEGER,
    passed_gate2 INTEGER,
    passed_gate3 INTEGER,
    total_qualified INTEGER,
    total_rejected INTEGER,
    total_manual_review INTEGER,
    platforms_detected INTEGER,
    estimated_cost REAL,
    output_file TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS company_run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    gate_reached INTEGER,
    details TEXT,
    created_at TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);
CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);
CREATE INDEX IF NOT EXISTS idx_companies_zoominfo_id ON companies(zoominfo_id);
CREATE INDEX IF NOT EXISTS idx_company_run_history_company_id ON company_run_history(company_id);
CREATE INDEX IF NOT EXISTS idx_company_run_history_run_id ON company_run_history(run_id);
"""


def _normalize_domain(domain: Optional[str]) -> Optional[str]:
    """Normalize domain for dedup: lowercase, strip protocol/www/trailing slash."""
    if not domain:
        return None
    d = domain.strip().lower()
    for prefix in ("https://", "http://", "www."):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.rstrip("/")
    return d or None


def _normalize_name(name: str) -> str:
    """Normalize company name for fuzzy matching."""
    import re
    n = name.strip().lower()
    # Remove common suffixes
    for suffix in [", inc.", ", inc", " inc.", " inc", ", llc", " llc",
                   ", corp.", " corp.", ", corp", " corp", ", ltd.", " ltd.",
                   ", ltd", " ltd", ", co.", " co.", " company", ", company"]:
        if n.endswith(suffix):
            n = n[:-len(suffix)]
    n = re.sub(r"[^a-z0-9\s]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


class Database:
    """Persistent SQLite database for lead qualification."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # --- Run management ---

    def start_run(self, input_file: str, settings: Dict[str, Any]) -> int:
        """Create a new run record. Returns run_id."""
        cursor = self.conn.execute(
            "INSERT INTO runs (started_at, status, input_file, settings_json) VALUES (?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), "running", input_file, json.dumps(settings)),
        )
        self.conn.commit()
        return cursor.lastrowid

    def complete_run(self, run_id: int, stats: Dict[str, Any], output_file: Optional[str] = None):
        """Mark a run as completed with stats."""
        self.conn.execute(
            """UPDATE runs SET
                completed_at=?, status=?, total_in_csv=?, new_companies=?,
                skipped_duplicates=?, rechecked=?, passed_gate1=?, passed_gate2=?,
                passed_gate3=?, total_qualified=?, total_rejected=?,
                total_manual_review=?, platforms_detected=?, estimated_cost=?, output_file=?
            WHERE id=?""",
            (
                datetime.utcnow().isoformat(), "completed",
                stats.get("total_in_csv", 0), stats.get("new_companies", 0),
                stats.get("skipped_duplicates", 0), stats.get("rechecked", 0),
                stats.get("passed_gate1", 0), stats.get("passed_gate2", 0),
                stats.get("passed_gate3", 0), stats.get("total_qualified", 0),
                stats.get("total_rejected", 0), stats.get("total_manual_review", 0),
                stats.get("platforms_detected", 0), stats.get("estimated_cost", 0.0),
                output_file, run_id,
            ),
        )
        self.conn.commit()

    def fail_run(self, run_id: int, notes: str = ""):
        """Mark a run as failed."""
        self.conn.execute(
            "UPDATE runs SET completed_at=?, status=?, notes=? WHERE id=?",
            (datetime.utcnow().isoformat(), "failed", notes, run_id),
        )
        self.conn.commit()

    def get_last_interrupted_run(self, input_file: str) -> Optional[int]:
        """Find a run that was interrupted for the given input file."""
        row = self.conn.execute(
            "SELECT id FROM runs WHERE input_file=? AND status IN ('running','interrupted') ORDER BY id DESC LIMIT 1",
            (input_file,),
        ).fetchone()
        return row["id"] if row else None

    # --- Dedup ---

    def find_existing(self, company: Company) -> Optional[Dict[str, Any]]:
        """Find an existing company in the database. Returns dict or None."""
        # Priority 1: ZoomInfo ID
        if company.zoominfo_id:
            row = self.conn.execute(
                "SELECT * FROM companies WHERE zoominfo_id=?", (company.zoominfo_id,)
            ).fetchone()
            if row:
                return dict(row)

        # Priority 2: Domain
        domain = _normalize_domain(company.domain)
        if domain:
            row = self.conn.execute(
                "SELECT * FROM companies WHERE domain=?", (domain,)
            ).fetchone()
            if row:
                return dict(row)

        # Priority 3: Name + City + State
        if company.name and company.city and company.state:
            norm_name = _normalize_name(company.name)
            # Get candidates and do fuzzy match in Python
            rows = self.conn.execute(
                "SELECT * FROM companies WHERE LOWER(state)=? AND city IS NOT NULL",
                (company.state.strip().lower(),),
            ).fetchall()
            for row in rows:
                row_dict = dict(row)
                if (row_dict.get("city") and
                        row_dict["city"].strip().lower() == company.city.strip().lower() and
                        _normalize_name(row_dict["name"]) == norm_name):
                    return row_dict

        return None

    def should_process(self, existing: Dict[str, Any], recheck: bool = False,
                       recheck_after: Optional[int] = None) -> Tuple[bool, str]:
        """Determine if an existing company should be re-processed.

        Returns (should_process, reason).
        """
        status = existing["status"]

        if status == "QUALIFIED":
            if recheck:
                return True, "recheck_qualified"
            return False, "already_qualified"

        if status == "REJECTED":
            if recheck:
                return True, "recheck_forced"
            if recheck_after is not None:
                last_updated = existing.get("last_updated_at")
                if last_updated:
                    try:
                        updated_dt = datetime.fromisoformat(last_updated)
                        if datetime.utcnow() - updated_dt > timedelta(days=recheck_after):
                            return True, "recheck_aged"
                    except (ValueError, TypeError):
                        pass
            # Auto-recheck review rejections after 90 days
            rejection_reason = existing.get("rejection_reason", "") or ""
            if "review" in rejection_reason.lower():
                last_updated = existing.get("last_updated_at")
                if last_updated:
                    try:
                        updated_dt = datetime.fromisoformat(last_updated)
                        if datetime.utcnow() - updated_dt > timedelta(days=90):
                            return True, "recheck_reviews_aged"
                    except (ValueError, TypeError):
                        pass
            return False, "already_rejected"

        if status == "MANUAL_REVIEW":
            return True, "reprocess_manual_review"

        if status == "PENDING":
            return True, "resume_pending"

        return False, "unknown_status"

    # --- Company CRUD ---

    def insert_company(self, company: Company, run_id: int) -> int:
        """Insert a new company. Returns the new company id."""
        now = datetime.utcnow().isoformat()
        domain = _normalize_domain(company.domain)
        cursor = self.conn.execute(
            """INSERT INTO companies
                (zoominfo_id, name, domain, city, state, phone, revenue, employees,
                 google_review_count, google_rating, detected_platforms, website_text,
                 service_type, ai_reasoning, status, rejection_reason,
                 first_seen_run_id, last_updated_run_id, first_seen_at, last_updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                company.zoominfo_id, company.name, domain, company.city,
                company.state, company.phone, company.revenue, company.employees,
                company.google_review_count, company.google_rating,
                company.detected_platforms, company.website_text,
                company.service_type, company.ai_reasoning, company.status,
                company.rejection_reason, run_id, run_id, now, now,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def update_company(self, company_id: int, company: Company, run_id: int):
        """Update an existing company record."""
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """UPDATE companies SET
                google_review_count=?, google_rating=?, detected_platforms=?,
                website_text=?, service_type=?, ai_reasoning=?, status=?,
                rejection_reason=?, last_updated_run_id=?, last_updated_at=?,
                revenue=COALESCE(?, revenue), employees=COALESCE(?, employees),
                phone=COALESCE(?, phone)
            WHERE id=?""",
            (
                company.google_review_count, company.google_rating,
                company.detected_platforms, company.website_text,
                company.service_type, company.ai_reasoning, company.status,
                company.rejection_reason, run_id, now,
                company.revenue, company.employees, company.phone,
                company_id,
            ),
        )
        self.conn.commit()

    def record_status_change(self, company_id: int, run_id: int,
                             old_status: Optional[str], new_status: str,
                             gate_reached: int, details: str = ""):
        """Record a status change in company_run_history."""
        self.conn.execute(
            """INSERT INTO company_run_history
                (company_id, run_id, old_status, new_status, gate_reached, details, created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (company_id, run_id, old_status, new_status, gate_reached, details,
             datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def get_company_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM companies").fetchone()
        return row["cnt"]

    def get_run_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM runs WHERE status='completed'").fetchone()
        return row["cnt"]

    # --- Queries for output ---

    def get_companies_by_status(self, status: str, run_id: Optional[int] = None) -> List[Dict]:
        """Get companies by status, optionally filtered to a specific run."""
        if run_id:
            rows = self.conn.execute(
                "SELECT * FROM companies WHERE status=? AND last_updated_run_id=? ORDER BY google_review_count DESC",
                (status, run_id),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM companies WHERE status=? ORDER BY google_review_count DESC",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_qualified(self) -> List[Dict]:
        """Get all qualified companies across all runs."""
        rows = self.conn.execute(
            """SELECT * FROM companies WHERE status='QUALIFIED'
               ORDER BY
                 CASE WHEN detected_platforms IS NOT NULL AND detected_platforms != '' THEN 0 ELSE 1 END,
                 google_review_count DESC""",
        ).fetchall()
        return [dict(r) for r in rows]

    def get_run_qualified(self, run_id: int) -> List[Dict]:
        """Get qualified companies from a specific run, sorted: platforms first, then by reviews."""
        rows = self.conn.execute(
            """SELECT * FROM companies WHERE status='QUALIFIED' AND last_updated_run_id=?
               ORDER BY
                 CASE WHEN detected_platforms IS NOT NULL AND detected_platforms != '' THEN 0 ELSE 1 END,
                 google_review_count DESC""",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Dashboard queries ---

    def query_latest_run(self) -> Optional[Dict]:
        row = self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def query_all_runs(self) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def query_cumulative_stats(self) -> Dict:
        total = self.conn.execute("SELECT COUNT(*) as c FROM companies").fetchone()["c"]
        qualified = self.conn.execute(
            "SELECT COUNT(*) as c FROM companies WHERE status='QUALIFIED'"
        ).fetchone()["c"]
        runs = self.conn.execute(
            "SELECT COUNT(*) as c FROM runs WHERE status='completed'"
        ).fetchone()["c"]
        total_cost = self.conn.execute(
            "SELECT COALESCE(SUM(estimated_cost), 0) as c FROM runs"
        ).fetchone()["c"]
        qual_rate = (qualified / total * 100) if total > 0 else 0
        return {
            "total_companies": total,
            "total_qualified": qualified,
            "total_runs": runs,
            "avg_qualification_rate": round(qual_rate, 1),
            "total_cost": round(total_cost, 2),
        }

    def query_funnel_data(self) -> Dict:
        """Aggregate funnel data across all runs."""
        row = self.conn.execute(
            """SELECT
                COALESCE(SUM(total_in_csv), 0) as total_input,
                COALESCE(SUM(passed_gate1), 0) as passed_gate1,
                COALESCE(SUM(passed_gate2), 0) as passed_gate2,
                COALESCE(SUM(passed_gate3), 0) as passed_gate3,
                COALESCE(SUM(total_qualified), 0) as total_qualified
            FROM runs WHERE status='completed'"""
        ).fetchone()
        return dict(row)

    def query_platform_breakdown(self) -> Dict[str, int]:
        """Count platform usage among qualified leads."""
        rows = self.conn.execute(
            "SELECT detected_platforms FROM companies WHERE status='QUALIFIED' AND detected_platforms IS NOT NULL AND detected_platforms != ''"
        ).fetchall()
        counts: Dict[str, int] = {}
        for row in rows:
            for platform in row["detected_platforms"].split(","):
                p = platform.strip()
                if p:
                    counts[p] = counts.get(p, 0) + 1
        # Also count those with no platform
        no_platform = self.conn.execute(
            "SELECT COUNT(*) as c FROM companies WHERE status='QUALIFIED' AND (detected_platforms IS NULL OR detected_platforms = '')"
        ).fetchone()["c"]
        if no_platform > 0:
            counts["No Platform Detected"] = no_platform
        return counts

    def query_review_distribution(self) -> List[int]:
        """Get review counts for companies that passed Gate 1."""
        rows = self.conn.execute(
            "SELECT google_review_count FROM companies WHERE google_review_count IS NOT NULL ORDER BY google_review_count"
        ).fetchall()
        return [row["google_review_count"] for row in rows]

    def query_status_changes(self) -> List[Dict]:
        """Get recent status changes between runs."""
        rows = self.conn.execute(
            """SELECT crh.*, c.name as company_name, c.domain
            FROM company_run_history crh
            JOIN companies c ON c.id = crh.company_id
            WHERE crh.old_status IS NOT NULL AND crh.old_status != crh.new_status
            ORDER BY crh.created_at DESC
            LIMIT 50"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats_summary(self) -> str:
        """Return a formatted string of database stats."""
        stats = self.query_cumulative_stats()
        runs = self.query_all_runs()

        lines = [
            f"Database: {self.db_path}",
            f"Total companies:     {stats['total_companies']}",
            f"Qualified leads:     {stats['total_qualified']}",
            f"Qualification rate:  {stats['avg_qualification_rate']}%",
            f"Total runs:          {stats['total_runs']}",
            f"Total API cost:      ${stats['total_cost']:.2f}",
            "",
        ]

        # Status breakdown
        for status in ["QUALIFIED", "REJECTED", "MANUAL_REVIEW", "PENDING"]:
            count = self.conn.execute(
                "SELECT COUNT(*) as c FROM companies WHERE status=?", (status,)
            ).fetchone()["c"]
            lines.append(f"  {status}: {count}")

        if runs:
            lines.append("")
            lines.append("Recent runs:")
            for run in runs[-5:]:
                lines.append(
                    f"  Run #{run['id']}: {run['started_at'][:19]} | "
                    f"{run.get('input_file', 'N/A')} | "
                    f"qualified={run.get('total_qualified', 0)} | "
                    f"cost=${run.get('estimated_cost', 0):.2f}"
                )

        return "\n".join(lines)
