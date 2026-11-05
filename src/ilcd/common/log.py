"""Logging: one format for every module, level from $ILCD_LOG_LEVEL (default INFO)."""

import logging
import os
import sys

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    root = logging.getLogger("ilcd")
    if not root.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter(_FORMAT, "%Y-%m-%d %H:%M:%S"))
        root.addHandler(h)
        root.setLevel(os.environ.get("ILCD_LOG_LEVEL", "INFO").upper())
        root.propagate = False
    return logging.getLogger(name if name.startswith("ilcd") else f"ilcd.{name}")
