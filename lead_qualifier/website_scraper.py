"""Gate 3a: Website scraping for platform detection and AI classification."""

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
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Pages to try scraping
PAGES_TO_TRY = [
    "",            # homepage
    "/services",
    "/about",
    "/about-us",
    "/our-services",
    "/residential",
]


def _scrape_page(url: str, session: requests.Session, timeout: int = 10) -> Optional[Tuple[str, str]]:
    """Scrape a single page. Returns (raw_html, clean_text) or None."""
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        if response.status_code != 200:
            return None

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            return None

        raw_html = response.text
        soup = BeautifulSoup(raw_html, "html.parser")

        # Remove script and style elements
        for element in soup(["script", "style", "nav", "footer", "header"]):
            element.decompose()

        clean_text = soup.get_text(separator=" ", strip=True)
        # Collapse whitespace
        clean_text = re.sub(r"\s+", " ", clean_text)

        return raw_html, clean_text

    except requests.RequestException as e:
        logger.debug(f"Failed to scrape {url}: {e}")
        return None


def scrape_website(company: Company, force: bool = False) -> Tuple[str, str]:
    """Scrape a company's website.

    Returns (raw_html_combined, clean_text_combined).
    If the company already has cached website_text and force is False, returns cached.
    """
    if not force and company.website_text:
        return "", company.website_text

    if not company.domain:
        return "", ""

    domain = company.domain.strip().lower()
    if not domain.startswith("http"):
        domain = "https://" + domain

    session = requests.Session()
    session.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    all_raw_html = []
    all_clean_text = []

    for page in PAGES_TO_TRY:
        url = domain.rstrip("/") + page
        result = _scrape_page(url, session)
        if result:
            raw_html, clean_text = result
            all_raw_html.append(raw_html)
            all_clean_text.append(clean_text)

        # Small delay between pages
        time.sleep(random.uniform(0.5, 1.0))

    raw_combined = "\n".join(all_raw_html)
    text_combined = " ".join(all_clean_text)

    # Cap clean text at ~4000 chars for AI
    if len(text_combined) > 4000:
        text_combined = text_combined[:4000]

    return raw_combined, text_combined


def scrape_websites_batch(companies: List[Company], force: bool = False,
                          callback=None) -> List[Company]:
    """Scrape websites for a batch of companies.

    Updates each company's website_text in place.
    Stores raw HTML temporarily for platform detection (returned separately).
    """
    total = len(companies)
    raw_htmls = {}

    for i, company in enumerate(companies):
        if not company.domain:
            if callback:
                callback(i + 1, total)
            continue

        try:
            raw_html, clean_text = scrape_website(company, force=force)
            company.website_text = clean_text
            raw_htmls[id(company)] = raw_html
        except Exception as e:
            logger.warning(f"Failed to scrape website for {company.name}: {e}")
            company.website_text = ""
            raw_htmls[id(company)] = ""

        if callback:
            callback(i + 1, total)

        # Delay between companies
        if i < total - 1:
            time.sleep(random.uniform(0.5, 1.5))

    return companies, raw_htmls
