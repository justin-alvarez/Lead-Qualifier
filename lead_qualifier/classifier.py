"""Gate 3c: Claude Haiku 4.5 classification via Batch API and realtime."""

import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import requests

from .models import Company

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com"
MODEL = "claude-haiku-4-5-20251001"

# Cost per million tokens (Batch API = 50% of realtime)
BATCH_INPUT_COST = 0.50 / 1_000_000
BATCH_OUTPUT_COST = 2.50 / 1_000_000
REALTIME_INPUT_COST = 1.00 / 1_000_000
REALTIME_OUTPUT_COST = 5.00 / 1_000_000

# Estimated tokens per company
EST_INPUT_TOKENS = 800
EST_OUTPUT_TOKENS = 100


def _get_api_key() -> Optional[str]:
    return os.environ.get("ANTHROPIC_API_KEY")


def _build_prompt(company: Company, service_type: str) -> str:
    """Build classification prompt based on service type filter."""
    base = (
        f"You are classifying an HVAC/plumbing/mechanical company based on their website content.\n\n"
        f"Company: {company.name}\n"
        f"Location: {company.city or 'Unknown'}, {company.state or 'Unknown'}\n\n"
        f"Website content:\n{company.website_text or '(no content available)'}\n\n"
    )

    if service_type == "residential":
        base += (
            "Classify this company as one of:\n"
            "- 'residential': Primarily serves residential customers (homeowners)\n"
            "- 'both': Serves both residential and commercial customers\n"
            "- 'commercial_only': Only serves commercial/industrial customers\n"
            "- 'unclear': Cannot determine from the available information\n\n"
        )
    elif service_type == "commercial":
        base += (
            "Classify this company as one of:\n"
            "- 'commercial': Primarily serves commercial/industrial customers\n"
            "- 'both': Serves both residential and commercial customers\n"
            "- 'residential_only': Only serves residential customers\n"
            "- 'unclear': Cannot determine from the available information\n\n"
        )
    else:
        base += (
            "Classify this company's service type as one of:\n"
            "- 'residential': Primarily serves residential customers\n"
            "- 'commercial': Primarily serves commercial customers\n"
            "- 'both': Serves both residential and commercial\n"
            "- 'unclear': Cannot determine\n\n"
        )

    base += (
        "Respond in JSON format:\n"
        '{"classification": "<type>", "reasoning": "<brief explanation>"}\n'
        "Respond with ONLY the JSON, no other text."
    )
    return base


def _parse_classification(text: str, service_type: str) -> Tuple[str, str]:
    """Parse AI classification response. Returns (service_type, reasoning)."""
    try:
        # Try to extract JSON from the response
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        classification = data.get("classification", "unclear").lower().strip()
        reasoning = data.get("reasoning", "")
        return classification, reasoning
    except (json.JSONDecodeError, KeyError, IndexError):
        return "unclear", f"Could not parse AI response: {text[:200]}"


def estimate_cost(num_companies: int, realtime: bool = False) -> float:
    """Estimate API cost for classifying N companies."""
    if realtime:
        input_cost = num_companies * EST_INPUT_TOKENS * REALTIME_INPUT_COST
        output_cost = num_companies * EST_OUTPUT_TOKENS * REALTIME_OUTPUT_COST
    else:
        input_cost = num_companies * EST_INPUT_TOKENS * BATCH_INPUT_COST
        output_cost = num_companies * EST_OUTPUT_TOKENS * BATCH_OUTPUT_COST
    return input_cost + output_cost


def classify_realtime(companies: List[Company], service_type: str,
                      callback=None) -> List[Company]:
    """Classify companies using realtime API calls."""
    api_key = _get_api_key()
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set. Marking all as MANUAL_REVIEW.")
        for company in companies:
            company.service_type = "unclear"
            company.ai_reasoning = "No API key available"
            company.status = "MANUAL_REVIEW"
        return companies

    headers = {
        "x-api-key": api_key,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    for i, company in enumerate(companies):
        prompt = _build_prompt(company, service_type)
        payload = {
            "model": MODEL,
            "max_tokens": 256,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            response = requests.post(
                f"{ANTHROPIC_API_URL}/v1/messages",
                headers=headers,
                json=payload,
                timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                text = result["content"][0]["text"]
                classification, reasoning = _parse_classification(text, service_type)
                company.service_type = classification
                company.ai_reasoning = reasoning
            else:
                logger.warning(f"API error {response.status_code} for {company.name}")
                company.service_type = "unclear"
                company.ai_reasoning = f"API error: {response.status_code}"
        except requests.RequestException as e:
            logger.warning(f"API request failed for {company.name}: {e}")
            company.service_type = "unclear"
            company.ai_reasoning = f"Request failed: {e}"

        if callback:
            callback(i + 1, len(companies))

    return companies


def classify_batch(companies: List[Company], service_type: str,
                   callback=None) -> List[Company]:
    """Classify companies using the Batch API for 50% cost savings."""
    api_key = _get_api_key()
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set. Marking all as MANUAL_REVIEW.")
        for company in companies:
            company.service_type = "unclear"
            company.ai_reasoning = "No API key available"
            company.status = "MANUAL_REVIEW"
        return companies

    headers = {
        "x-api-key": api_key,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    # Build batch requests
    batch_requests = []
    for i, company in enumerate(companies):
        prompt = _build_prompt(company, service_type)
        batch_requests.append({
            "custom_id": str(i),
            "params": {
                "model": MODEL,
                "max_tokens": 256,
                "messages": [{"role": "user", "content": prompt}],
            },
        })

    # Submit batch
    try:
        response = requests.post(
            f"{ANTHROPIC_API_URL}/v1/messages/batches",
            headers=headers,
            json={"requests": batch_requests},
            timeout=60,
        )

        if response.status_code not in (200, 201):
            logger.error(f"Batch submit failed: {response.status_code} {response.text}")
            # Fall back to marking as manual review
            for company in companies:
                company.service_type = "unclear"
                company.ai_reasoning = "Batch submission failed"
                company.status = "MANUAL_REVIEW"
            return companies

        batch_data = response.json()
        batch_id = batch_data["id"]
        logger.info(f"Batch submitted: {batch_id}")

    except requests.RequestException as e:
        logger.error(f"Batch submit request failed: {e}")
        for company in companies:
            company.service_type = "unclear"
            company.ai_reasoning = f"Batch submit failed: {e}"
            company.status = "MANUAL_REVIEW"
        return companies

    # Poll for completion
    poll_count = 0
    while True:
        time.sleep(30)
        poll_count += 1

        try:
            status_response = requests.get(
                f"{ANTHROPIC_API_URL}/v1/messages/batches/{batch_id}",
                headers=headers,
                timeout=30,
            )

            if status_response.status_code != 200:
                logger.warning(f"Batch poll error: {status_response.status_code}")
                if poll_count > 60:  # 30 minutes max
                    break
                continue

            status_data = status_response.json()
            processing_status = status_data.get("processing_status", "")

            if callback:
                callback(poll_count, processing_status)

            if processing_status == "ended":
                break

            if poll_count > 60:
                logger.error("Batch timed out after 30 minutes")
                break

        except requests.RequestException as e:
            logger.warning(f"Batch poll request failed: {e}")
            if poll_count > 60:
                break

    # Retrieve results
    try:
        results_response = requests.get(
            f"{ANTHROPIC_API_URL}/v1/messages/batches/{batch_id}/results",
            headers=headers,
            timeout=60,
        )

        if results_response.status_code != 200:
            logger.error(f"Batch results failed: {results_response.status_code}")
            for company in companies:
                company.service_type = "unclear"
                company.ai_reasoning = "Failed to retrieve batch results"
                company.status = "MANUAL_REVIEW"
            return companies

        # Parse JSONL results
        for line in results_response.text.strip().split("\n"):
            if not line.strip():
                continue
            try:
                result = json.loads(line)
                custom_id = int(result["custom_id"])
                if result.get("result", {}).get("type") == "succeeded":
                    message = result["result"]["message"]
                    text = message["content"][0]["text"]
                    classification, reasoning = _parse_classification(text, service_type)
                    companies[custom_id].service_type = classification
                    companies[custom_id].ai_reasoning = reasoning
                else:
                    error = result.get("result", {}).get("error", {}).get("message", "Unknown error")
                    companies[custom_id].service_type = "unclear"
                    companies[custom_id].ai_reasoning = f"Batch error: {error}"
            except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
                logger.warning(f"Failed to parse batch result line: {e}")

    except requests.RequestException as e:
        logger.error(f"Batch results request failed: {e}")
        for company in companies:
            if not company.service_type:
                company.service_type = "unclear"
                company.ai_reasoning = f"Batch results failed: {e}"
                company.status = "MANUAL_REVIEW"

    return companies


def apply_service_filter(companies: List[Company],
                         service_type: str) -> Tuple[List[Company], List[Company], List[Company]]:
    """Apply service type filter after classification.

    Returns (qualified, rejected, manual_review).
    """
    qualified = []
    rejected = []
    manual_review = []

    for company in companies:
        stype = (company.service_type or "unclear").lower().strip()

        if stype == "unclear":
            company.status = "MANUAL_REVIEW"
            company.rejection_reason = "AI classification unclear"
            company.gate_reached = 3
            manual_review.append(company)
            continue

        if service_type == "residential":
            if stype in ("residential", "both"):
                company.status = "QUALIFIED"
                company.gate_reached = 3
                qualified.append(company)
            else:
                company.status = "REJECTED"
                company.rejection_reason = f"Commercial only (AI classified as {stype})"
                company.gate_reached = 3
                rejected.append(company)

        elif service_type == "commercial":
            if stype in ("commercial", "both"):
                company.status = "QUALIFIED"
                company.gate_reached = 3
                qualified.append(company)
            else:
                company.status = "REJECTED"
                company.rejection_reason = f"Residential only (AI classified as {stype})"
                company.gate_reached = 3
                rejected.append(company)

        else:  # "any" — should not normally reach here since we skip AI
            company.status = "QUALIFIED"
            company.gate_reached = 3
            qualified.append(company)

    return qualified, rejected, manual_review
