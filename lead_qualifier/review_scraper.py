"""Gate 2: Google review lookup via Places API."""

import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import requests

from .models import Company

logger = logging.getLogger(__name__)


def _get_google_api_key() -> Optional[str]:
    return os.environ.get("GOOGLE_API_KEY")


def search_place(company: Company, api_key: str) -> Tuple[Optional[int], Optional[float]]:
    """Look up a company on Google Places API and return (review_count, rating)."""
    query_parts = [company.name]
    if company.city:
        query_parts.append(company.city)
    if company.state:
        query_parts.append(company.state)
    query = " ".join(query_parts)

    try:
        # Use Text Search to find the place
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            "query": query,
            "key": api_key,
        }
        response = requests.get(url, params=params, timeout=10)

        if response.status_code != 200:
            logger.warning(f"Google Places API returned {response.status_code} for '{query}'")
            return None, None

        data = response.json()

        if data.get("status") != "OK" or not data.get("results"):
            logger.debug(f"No results for '{query}': {data.get('status')}")
            return None, None

        # Take the first result
        place = data["results"][0]
        review_count = place.get("user_ratings_total")
        rating = place.get("rating")

        return review_count, rating

    except requests.RequestException as e:
        logger.warning(f"Google Places API request failed for '{query}': {e}")
        return None, None


def scrape_reviews_batch(companies: List[Company], callback=None) -> List[Company]:
    """Look up Google reviews for a batch of companies via Places API.

    Updates each company's google_review_count and google_rating in place.
    """
    api_key = _get_google_api_key()
    if not api_key:
        logger.warning("GOOGLE_API_KEY not set. Cannot look up reviews.")
        if callback:
            for i in range(len(companies)):
                callback(i + 1, len(companies), blocked=True)
        return companies

    total = len(companies)

    for i, company in enumerate(companies):
        review_count, rating = search_place(company, api_key)
        company.google_review_count = review_count
        company.google_rating = rating

        if callback:
            callback(i + 1, total, blocked=False)

        # Small delay to stay within rate limits
        if i < total - 1:
            time.sleep(0.1)

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
            company.status = "MANUAL_REVIEW"
            company.rejection_reason = "Could not determine Google review count"
            company.gate_reached = 2
            manual_review.append(company)
        elif company.google_review_count < min_reviews:
            company.status = "REJECTED"
            company.rejection_reason = f"Google reviews {company.google_review_count} below minimum {min_reviews}"
            company.gate_reached = 2
            rejected.append(company)
        else:
            company.gate_reached = 2
            passed.append(company)

    return passed, rejected, manual_review
