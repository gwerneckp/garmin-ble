"""Logging configuration for garmin_ble.

Usage
-----
    from garmin_ble.logging import get_logger

    log = get_logger(__name__)
    log.info("Connecting to %s ...", address)
"""

import logging
import sys

_PACKAGE_ROOT = __name__.rpartition(".")[0]  # "garmin_ble"

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_DEFAULT_DATE = "%H:%M:%S"


def configure(
    *,
    level: int = logging.INFO,
    fmt: str = _DEFAULT_FORMAT,
    datefmt: str = _DEFAULT_DATE,
    stream=sys.stderr,
) -> None:
    """Set up a root-level handler for the ``garmin_ble`` package logger.

    Call this once at application startup (e.g. in ``if __name__ == "__main__"``
    or in your CLI entry point).  If you prefer full control, feel free to
    ignore this function and configure ``logging.getLogger("garmin_ble")``
    yourself.
    """
    logger = logging.getLogger(_PACKAGE_ROOT)
    logger.setLevel(level)

    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))

    # Avoid duplicate handlers on repeated calls.
    logger.handlers.clear()
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger of the ``garmin_ble`` package tree.

    Use this in every module::

        from garmin_ble.logging import get_logger
        log = get_logger(__name__)
    """
    # Ensure the name is nested under the package root so users can
    # control the whole tree via ``logging.getLogger("garmin_ble")``.
    if not name.startswith(_PACKAGE_ROOT + "."):
        name = f"{_PACKAGE_ROOT}.{name}"
    return logging.getLogger(name)
