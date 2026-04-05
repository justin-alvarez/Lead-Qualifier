"""Configuration loading: defaults → YAML config file → CLI flags."""

import os
import re
from typing import Any, Dict, Optional

import yaml


DEFAULTS = {
    "min_revenue": 2_000_000,
    "min_employees": 5,
    "min_reviews": 200,
    "service_type": "residential",
    "db": "./lead_qualifier.db",
}


def parse_revenue_shorthand(value: str) -> float:
    """Parse revenue shorthand like '2M', '500K', '1.5B', '$2,500,000'."""
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip().upper().replace("$", "").replace(",", "")

    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}

    match = re.match(r"^([\d.]+)\s*([KMB])?$", s)
    if match:
        num = float(match.group(1))
        suffix = match.group(2)
        if suffix:
            num *= multipliers[suffix]
        return num

    try:
        return float(s)
    except ValueError:
        raise ValueError(f"Cannot parse revenue value: {value}")


def load_yaml_config(path: str) -> Dict[str, Any]:
    """Load configuration from a YAML file."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    # Parse revenue shorthand in config file
    if "min_revenue" in data and isinstance(data["min_revenue"], str):
        data["min_revenue"] = parse_revenue_shorthand(data["min_revenue"])

    return data


def build_config(cli_args) -> Dict[str, Any]:
    """Build final config by merging defaults → YAML → CLI flags.

    CLI flags always win. YAML overrides defaults. Missing values use defaults.
    """
    config = dict(DEFAULTS)

    # Load YAML config if specified
    if getattr(cli_args, "config", None):
        yaml_config = load_yaml_config(cli_args.config)
        # Parse revenue in yaml
        if "min_revenue" in yaml_config:
            if isinstance(yaml_config["min_revenue"], str):
                yaml_config["min_revenue"] = parse_revenue_shorthand(yaml_config["min_revenue"])
        config.update(yaml_config)

    # CLI flags override everything
    cli_overrides = {}
    if getattr(cli_args, "min_revenue", None) is not None:
        cli_overrides["min_revenue"] = parse_revenue_shorthand(str(cli_args.min_revenue))
    if getattr(cli_args, "min_employees", None) is not None:
        cli_overrides["min_employees"] = int(cli_args.min_employees)
    if getattr(cli_args, "min_reviews", None) is not None:
        cli_overrides["min_reviews"] = int(cli_args.min_reviews)
    if getattr(cli_args, "service_type", None) is not None:
        cli_overrides["service_type"] = cli_args.service_type
    if getattr(cli_args, "db", None) is not None:
        cli_overrides["db"] = cli_args.db

    config.update(cli_overrides)

    # Copy all other CLI args
    for key in ["input", "output", "limit", "resume", "recheck", "recheck_after",
                 "force_scrape", "skip_reviews", "skip_ai", "dry_run", "realtime",
                 "dashboard_only", "export_all", "stats", "dashboard", "config"]:
        val = getattr(cli_args, key, None)
        if val is not None:
            config[key] = val

    # Ensure boolean flags default to False
    for key in ["resume", "recheck", "force_scrape", "skip_reviews", "skip_ai",
                 "dry_run", "realtime", "dashboard_only", "export_all", "stats"]:
        if key not in config:
            config[key] = False

    return config


def format_revenue(value: Optional[float]) -> str:
    """Format revenue for display."""
    if value is None:
        return "N/A"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.1f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:,.0f}K"
    return f"${value:,.0f}"
