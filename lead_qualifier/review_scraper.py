"""Gate 2: Google review lookup via Apify Google Maps Scraper."""

import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import requests

from .models import Company

logger = logging.getLogger(__name__)


def _get_apify_token() -> Optional[str]:
    return os.environ.get("APIFY_API_TOKEN")


def _get_google_api_key() -> Optional[str]:
    return os.environ.get("GOOGLE_API_KEY")


# --- Apify Google Maps Scraper ---

def _search_apify_batch(companies: List[Company], token: str, callback=None) -> List[Company]:
    """Use Apify Google Maps Scraper to get reviews for a batch of companies."""

    # Build search queries
    queries = []
    for company in companies:
        parts = [company.name]
        if company.city:
            parts.append(company.city)
        if company.state:
            parts.append(company.state)
        queries.append(" ".join(parts))

    # Start the actor run
    actor_id = "nwua9Gu5YrADL7ZDj"  # apify/google-maps-scraper
    url = f"https://api.apify.com/v2/acts/{actor_id}/runs"

    payload = {
        "searchStringsArray": queries,
        "maxCrawledPlacesPerSearch": 1,
        "language": "en",
        "onlyDataFromSearchPage": True,
    }

    headers = {"Content-Type": "application/json"}
    params = {"token": token}

    try:
        logger.info(f"Starting Apify scraper for {len(queries)} companies...")
        response = requests.post(url, json=payload, headers=headers, params=params, timeout=30)

        if response.status_code not in (200, 201):
            logger.error(f"Apify start failed: {response.status_code} {response.text[:200]}")
            return companies

        run_data = response.json().get("data", {})
        run_id = run_data.get("id")
        dataset_id = run_data.get("defaultDatasetId")

        if not run_id:
            logger.error("Apify did not return a run ID")
            return companies

        logger.info(f"Apify run started: {run_id}")

        # Poll for completion
        poll_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
        poll_count = 0
        while True:
            time.sleep(10)
            poll_count += 1

            status_resp = requests.get(poll_url, params={"token": token}, timeout=15)
            if status_resp.status_code != 200:
                logger.warning(f"Apify poll error: {status_resp.status_code}")
                if poll_count > 60:
                    break
                continue

            status = status_resp.json().get("data", {}).get("status", "")
            if callback:
                callback(poll_count, len(companies), status=status)

            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                if status != "SUCCEEDED":
                    logger.error(f"Apify run ended with status: {status}")
                break

            if poll_count > 60:
                logger.error("Apify run timed out after 10 minutes")
                break

        # Fetch results from dataset
        if not dataset_id:
            return companies

        results_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
        results_resp = requests.get(results_url, params={"token": token, "format": "json"}, timeout=30)

        if results_resp.status_code != 200:
            logger.error(f"Apify results fetch failed: {results_resp.status_code}")
            return companies

        results = results_resp.json()

        # Match results back to companies by search query
        # Results come in the same order as searchStringsArray
        for result in results:
            search_string = result.get("searchString", "")
            review_count = result.get("reviewsCount") or result.get("totalScore", None)
            rating = result.get("totalScore")
            reviews = result.get("reviewsCount")

            # Find matching company
            for i, query in enumerate(queries):
                if query == search_string and i < len(companies):
                    companies[i].google_review_count = reviews
                    companies[i].google_rating = rating
                    break
            else:
                # Try matching by company name in the result title
                title = (result.get("title") or "").lower()
                for company in companies:
                    if company.google_review_count is None and company.name.lower() in title:
                        company.google_review_count = reviews
                        company.google_rating = rating
                        break

    except requests.RequestException as e:
        logger.error(f"Apify request failed: {e}")

    return companies


# --- Google Places API fallback ---

def _search_places_api(company: Company, api_key: str) -> Tuple[Optional[int], Optional[float]]:
    """Look up a company on Google Places API."""
    query_parts = [company.name]
    if company.city:
        query_parts.append(company.city)
    if company.state:
        query_parts.append(company.state)
    query = " ".join(query_parts)

    try:
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {"query": query, "key": api_key}
        response = requests.get(url, params=params, timeout=10)

        if response.status_code != 200:
            return None, None

        data = response.json()
        if data.get("status") != "OK" or not data.get("results"):
            return None, None

        place = data["results"][0]
        return place.get("user_ratings_total"), place.get("rating")

    except requests.RequestException:
        return None, None


# --- Main entry point ---

def scrape_reviews_batch(companies: List[Company], callback=None) -> List[Company]:
    """Look up Google reviews for a batch of companies.

    Tries Apify first (free tier), falls back to Google Places API.
    """
    apify_token = _get_apify_token()
    google_key = _get_google_api_key()

    if apify_token:
        logger.info("Using Apify Google Maps Scraper for reviews")

        def apify_callback(poll_count, total, status=""):
            if callback:
                callback(poll_count, total, blocked=False)

        _search_apify_batch(companies, apify_token, callback=apify_callback)

        # Check how many we got
        found = sum(1 for c in companies if c.google_review_count is not None)
        logger.info(f"Apify found reviews for {found}/{len(companies)} companies")
        return companies

    elif google_key:
        logger.info("Using Google Places API for reviews")
        total = len(companies)
        for i, company in enumerate(companies):
            review_count, rating = _search_places_api(company, google_key)
            company.google_review_count = review_count
            company.google_rating = rating
            if callback:
                callback(i + 1, total, blocked=False)
            if i < total - 1:
                time.sleep(0.1)
        return companies

    else:
        logger.warning(
            "No APIFY_API_TOKEN or GOOGLE_API_KEY set. Cannot look up reviews. "
            "Set one of these environment variables to enable review lookup."
        )
        if callback:
            for i in range(len(companies)):
                callback(i + 1, len(companies), blocked=True)
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
