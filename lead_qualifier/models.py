"""Data models for the lead qualifier pipeline."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Company:
    """Represents a company being processed through the pipeline."""

    name: str
    domain: Optional[str] = None
    zoominfo_id: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    phone: Optional[str] = None
    revenue: Optional[float] = None
    employees: Optional[int] = None
    google_review_count: Optional[int] = None
    google_rating: Optional[float] = None
    detected_platforms: Optional[str] = None
    website_text: Optional[str] = None
    service_type: Optional[str] = None
    ai_reasoning: Optional[str] = None
    status: str = "PENDING"
    rejection_reason: Optional[str] = None
    db_id: Optional[int] = None
    first_seen_run_id: Optional[int] = None
    last_updated_run_id: Optional[int] = None
    gate_reached: int = 0
    old_status: Optional[str] = None  # for tracking changes

    # Track which gate this company reached in the current run
    _skipped: bool = field(default=False, repr=False)
