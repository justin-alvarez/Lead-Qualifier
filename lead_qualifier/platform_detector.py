"""Gate 3b: Service management platform detection from website HTML."""

import re
from typing import Dict, List

from .models import Company


PLATFORM_SIGNATURES = {
    "ServiceTitan": ["servicetitan.com", "servicetitan.io", "schedule.servicetitan.com"],
    "Housecall Pro": ["housecallpro.com", "booking.housecallpro.com"],
    "Jobber": ["getjobber.com", "app.jobber.com", "clienthub.getjobber.com"],
    "FieldEdge": ["fieldedge.com", "fieldedge.net"],
    "Schedule Engine": ["scheduleengine.com", "scheduleengine.net"],
    "Successware": ["successware.com"],
    "Service Fusion": ["servicefusion.com"],
    "Sera Systems": ["sera.tech", "serasystems.com"],
    "ServiceMax": ["servicemax.com"],
    "Dispatch": ["dispatch.me"],
    "XOi Technologies": ["xoi.io", "xoitech.com"],
    "BuildOps": ["buildops.com"],
    "Payzer": ["payzerware.com", "payzer.com"],
    "WorkWave": ["workwave.com"],
    "Zuper": ["zuper.co"],
}


def detect_platforms(raw_html: str) -> List[str]:
    """Detect service management platforms in raw HTML.

    Checks <script src>, <iframe src>, <a href>, and "Powered by" text.
    Returns list of detected platform names.
    """
    if not raw_html:
        return []

    html_lower = raw_html.lower()
    detected = []

    for platform, signatures in PLATFORM_SIGNATURES.items():
        for sig in signatures:
            if sig.lower() in html_lower:
                if platform not in detected:
                    detected.append(platform)
                break

    return detected


def detect_platforms_batch(companies: List[Company],
                           raw_htmls: Dict[int, str]) -> Dict[str, int]:
    """Detect platforms for a batch of companies.

    Updates each company's detected_platforms field in place.
    Returns platform count summary.
    """
    platform_counts: Dict[str, int] = {}

    for company in companies:
        raw_html = raw_htmls.get(id(company), "")
        platforms = detect_platforms(raw_html)

        if platforms:
            company.detected_platforms = ", ".join(platforms)
            for p in platforms:
                platform_counts[p] = platform_counts.get(p, 0) + 1
        else:
            company.detected_platforms = ""

    return platform_counts
