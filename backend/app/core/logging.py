"""Logging setup shared by the API and scripts."""

import logging


def configure_logging(level: str) -> None:
    """Configure root logging with a single, grep-friendly line format."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
