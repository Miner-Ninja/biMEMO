"""Structured logging configuration for biMEMO processes.

Configures structlog with JSON output in production and colored console
output in debug mode. Call configure_logging() once at the start of
each subprocess target before any log calls are made.
"""

import logging
import sys

import structlog


def _log_level(debug: bool) -> int:
    """Return the stdlib log level for one runtime mode."""
    return logging.DEBUG if debug else logging.INFO


def _shared_processors() -> list[structlog.types.Processor]:
    """Return processors used by both console and JSON logging."""
    return [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]


def _processors_for_mode(debug: bool) -> list[structlog.types.Processor]:
    """Return the full processor chain for one runtime mode."""
    processors = _shared_processors()
    if debug:
        return [*processors, structlog.dev.ConsoleRenderer()]
    return [
        *processors,
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]


def _configure_stdlib_logging(level: int) -> None:
    """Keep stdlib logging aligned with structlog output."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )


def _configure_structlog(
    *,
    level: int,
    processors: list[structlog.types.Processor],
) -> None:
    """Apply the structlog configuration for the current process."""
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def configure_logging(debug: bool) -> None:
    """Configure structlog for the current process.

    In debug mode: colored, human-readable console output.
    In production mode: JSON output suitable for log aggregators.

    Args:
        debug: True for colored console output, False for JSON.
    """
    level = _log_level(debug)
    processors = _processors_for_mode(debug)

    _configure_structlog(
        level=level,
        processors=processors,
    )

    _configure_stdlib_logging(level)
