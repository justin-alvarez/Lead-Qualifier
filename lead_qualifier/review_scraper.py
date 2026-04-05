"""Gate 2: Google review scraping (free, no API)."""

import logging
import random
import re
import time
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from .models import Company

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
]


def _get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def _parse_review_count(text: str) -> Optional[int]:
    """Extract review count from text like '1,234 reviews' or '(1,234)'."""
    patterns = [
        r"([\d,]+)\s+(?:Google\s+)?reviews?",
        r"([\d,]+)\s+ratings?",
        r"\(([\d,]+)\)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            count_str = match.group(1).replace(",", "")
            try:
                return int(count_str)
            except ValueError:
                continue
    return None


def _parse_rating(text: str) -> Optional[float]:
    """Extract rating from text like '4.5 stars' or '4.5/5'."""
    match = re.search(r"(\d+\.?\d*)\s*(?:stars?|/\s*5|out of 5)", text, re.IGNORECASE)
    if match:
        try:
            rating = float(match.group(1))
            if 0 <= rating <= 5:
                return rating
        except ValueError:
            pass
    # Try standalone pattern near "rating" text
    match = re.search(r"(\d+\.\d+)", text)
    if match:
        try:
            rating = float(match.group(1))
            if 1.0 <= rating <= 5.0:
                return rating
        except ValueError:
            pass
    return None


def scrape_google_reviews(company: Company) -> Tuple[Optional[int], Optional[float]]:
    """Scrape Google for review count and rating.

    Returns (review_count, rating) or (None, None) on failure.
    """
    query_parts = [company.name]
    if company.city:
        query_parts.append(company.city)
    if company.state:
        query_parts.append(company.state)
    query = " ".join(query_parts)

    session = _get_session()

    try:
        # Search Google
        url = "https://www.google.com/search"
        params = {"q": query, "hl": "en"}
        response = session.get(url, params=params, timeout=10)

        if response.status_code == 429:
            logger.warning("Google rate limited (429). Backing off.")
            return None, None

        if response.status_code != 200:
            logger.warning(f"Google returned {response.status_code} for '{query}'")
            return None, None

        soup = BeautifulSoup(response.text, "html.parser")
        page_text = soup.get_text()

        review_count = _parse_review_count(page_text)
        rating = _parse_rating(page_text)

        return review_count, rating

    except requests.RequestException as e:
        logger.warning(f"Request failed for '{query}': {e}")
        return None, None


def scrape_reviews_batch(companies: List[Company], callback=None) -> List[Company]:
    """Scrape Google reviews for a batch of companies.

    Updates each company's google_review_count and google_rating in place.
    Calls callback(i, total) after each company for progress reporting.
    """
    total = len(companies)
    blocked = False
    consecutive_failures = 0

    for i, company in enumerate(companies):
        if blocked:
            company.google_review_count = None
            company.google_rating = None
            if callback:
                callback(i + 1, total, blocked=True)
            continue

        # Random delay between requests
        if i > 0:
            delay = random.uniform(1.5, 3.0)
            time.sleep(delay)

        review_count, rating = scrape_google_reviews(company)
        company.google_review_count = review_count
        company.google_rating = rating

        if review_count is None and rating is None:
            consecutive_failures += 1
            if consecutive_failures >= 10:
                logger.warning(
                    f"Google scraping appears blocked after {i + 1} attempts. "
                    f"Marking remaining {total - i - 1} companies for manual review."
                )
                blocked = True
        else:
            consecutive_failures = 0

        if callback:
            callback(i + 1, total, blocked=False)

    return companies


def filter_by_reviews(companies: List[Company], min_reviews: int) -> Tuple[List[Company], List[Company], List[Company]]:
    """Filter companies by minimum Google review count.

    Returns (passed, rejected, manual_review).
    """
    passed = []
    rejected = []
    manual_review = []

    for company in companies:
        if company.google_review_count is None:
            # Don't block the pipeline — let them through with a note
            company.gate_reached = 2
            passed.append(company)
        elif company.google_review_count < min_reviews:
            company.status = "REJECTED"
            company.rejection_reason = f"Google reviews {company.google_review_count} below minimum {min_reviews}"
            company.gate_reached = 2
            rejected.append(company)
        else:
            company.gate_reached = 2
            passed.append(company)

    return passed, rejected, manual_review
