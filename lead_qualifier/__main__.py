"""Entry point for `python -m lead_qualifier`."""

import logging
import sys

from .cli import parse_args
from .config import build_config
from .pipeline import run_pipeline


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    args = parse_args()
    config = build_config(args)
    run_pipeline(config)


if __name__ == "__main__":
    main()
