#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import logging
import sys
from logging.handlers import WatchedFileHandler
from os import PathLike
from pathlib import Path
from typing import IO

from cmk.utils.paths import log_dir

from ._level import VERBOSE

__all__ = [
    "clear_console_logging",
    "get_formatter",
    "init_dedicated_logging",
    "logger",
    "modify_logging_handler",
    "open_log",
    "setup_console_logging",
    "setup_logging_handler",
    "setup_watched_file_logging_handler",
    "verbosity_to_log_level",
]

IOLog = IO[str]

logger = logging.getLogger("cmk")


def get_formatter(
    format_str: str = "%(asctime)s [%(levelno)s] [%(name)s %(process)d] %(message)s",
) -> logging.Formatter:
    """Returns a new message formater instance that uses the standard
    Check_MK log format by default. You can also set another format
    if you like."""
    return logging.Formatter(format_str)


def clear_console_logging() -> None:
    logger.handlers[:] = []
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.INFO)


# Set default logging handler to avoid "No handler found" warnings.
# Python 2.7+
clear_console_logging()


def setup_console_logging() -> None:
    """This method enables all log messages to be written to the console
    without any additional information like date/time, logger-name. Just
    the log line is written.

    This can be used for existing command line applications which were
    using sys.stdout.write() or print() before.
    """
    setup_logging_handler(sys.stdout, get_formatter("%(message)s"))


def open_log(log_file_path: str | Path) -> IOLog:
    """Open logfile and fall back to stderr if this is not successfull
    The opened file-like object is returned.
    """
    if not isinstance(log_file_path, Path):
        log_file_path = Path(log_file_path)

    try:
        logfile: IOLog = log_file_path.open("a", encoding="utf-8")
        logfile.flush()
    except Exception as e:
        logger.exception("Cannot open log file '%s': %s", log_file_path, e)
        logfile = sys.stderr
    setup_logging_handler(logfile)
    return logfile


def setup_watched_file_logging_handler(
    logfile: str | PathLike[str], formatter: logging.Formatter | None = None
) -> None:
    """Removes all previous logger handlers and set a logfile handler for the given logfile path
    This handler automatically reopens the logfile if it detects an inode change, e.g through logrotate
    """
    if formatter is None:
        formatter = get_default_formatter()

    handler = WatchedFileHandler(logfile)
    handler.setFormatter(formatter)
    del logger.handlers[:]  # Remove all previously existing handlers
    logger.addHandler(handler)


def setup_logging_handler(stream: IOLog, formatter: logging.Formatter | None = None) -> None:
    """This method enables all log messages to be written to the given
    stream file object. The messages are formatted in Check_MK standard
    logging format.
    """
    if formatter is None:
        formatter = get_default_formatter()

    handler = logging.StreamHandler(stream=stream)
    handler.setFormatter(formatter)

    del logger.handlers[:]  # Remove all previously existing handlers
    logger.addHandler(handler)


def get_default_formatter() -> logging.Formatter:
    return get_formatter("%(asctime)s [%(levelno)s] [%(name)s] %(message)s")


def modify_logging_handler(
    handler: logging.Handler,
    formatter: logging.Formatter | None,
) -> None:
    """Changes logging behavior. Normally used by fetcher to prevent
    non-formatted output to stdout"""

    if formatter is not None:
        handler.setFormatter(formatter)

    del logger.handlers[:]  # Remove all previously existing handlers
    logger.addHandler(handler)


def verbosity_to_log_level(verbosity: int) -> int:
    """Values for "verbosity":

    0: enables INFO and above
    1: enables VERBOSE and above
    2: enables DEBUG and above (ALL messages)
    """
    if verbosity == 0:
        return logging.INFO
    if verbosity == 1:
        return VERBOSE
    if verbosity >= 2:
        return logging.DEBUG
    raise NotImplementedError()


def init_dedicated_logging(
    log_level: int | None, target_logger: logging.Logger, log_file: Path
) -> None:
    """Initializes logging to a dedicated log_file for the given log_handler.
    Logging won't be propagated to parent loggers of log_handler."""
    del target_logger.handlers[:]  # Remove all previously existing handlers

    if log_level is None:
        target_logger.addHandler(logging.NullHandler())
        target_logger.propagate = False
        return

    handler = logging.FileHandler(
        Path(log_dir) / log_file,
        encoding="UTF-8",
    )
    handler.setFormatter(get_formatter())
    target_logger.setLevel(log_level)
    target_logger.addHandler(handler)
    target_logger.propagate = False
