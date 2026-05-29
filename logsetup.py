#!/usr/bin/env python

"""
logsetup.py — A small helper for consistent logging configuration.

Provides setup_logging() plus the normal and colored formatters that have
been reused (and slowly drifted) across many personal scripts.

The module is designed to live in a shared location and be symlinked into
individual script directories so that a plain `import logsetup` works.

Typical usage (only from a script's entry point):

    import logsetup
    logsetup.setup_logging(
        log_file="app.log",
        debug_modules=[__name__, "worker", "mylib"],
    )

Design goals:
- One place to evolve the format strings, color palette, and handler logic.
- Auto color detection that respects terminals and the NO_COLOR convention.
- Always-on defensive filters for known noisy libraries (without polluting
  the public API).
- Minimal surface: one primary function + a couple of exported constants.
"""

import logging
import os
import sys
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# Additional logging level for extra info we usually don't even want
# during debugging runs. Used via _LOGGER.log(DEBUG_EXTRA, ...).
DEBUG_EXTRA = 5

# Logging color mappings, when sent to stderr/terminal.
LOG_COLORS = {
    "DEBUG": "thin_white",
    "INFO": "white",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold_red,bg_white",
}

CLASSIC_DATEFMT = "%m/%d %H:%M:%S"

COLORED_FORMAT = (
    "[%(log_color)s%(asctime)s%(reset)s|%(module)-10s:%(lineno)-4d]"
    " %(log_color)s%(levelname)-8s%(reset)s: %(log_color)s%(message)s%(reset)s"
)


def _strip_trailing_newlines(record: logging.LogRecord) -> bool:
    """
    Filter installed on every handler created by this module.

    Some libraries emit log messages that end with literal newlines.
    This pollutes terminal output and log files. We strip only the msg
    itself (simple and sufficient).
    """
    if isinstance(record.msg, str):
        record.msg = record.msg.strip()
    return True


def _setup_handler(
    h: logging.Handler, *, level: int = logging.DEBUG, colored: bool = False
) -> logging.Handler:
    """
    Attach either a ColoredFormatter or a plain Formatter to the handler,
    plus the universal trailing-newline stripper.

    The two format strings and the color palette are intentionally the
    only "style" that this module enforces.
    """
    if colored:
        try:
            from colorlog import ColoredFormatter
        except ImportError as e:
            raise ImportError(
                "logsetup: color requested but the 'colorlog' package is not installed. "
                "Install it (pip install colorlog) or call setup_logging(force_color=False)."
            ) from e
        formatter = ColoredFormatter(
            COLORED_FORMAT,
            datefmt=CLASSIC_DATEFMT,
            reset=True,
            log_colors=LOG_COLORS,
            secondary_log_colors={},
            style="%",
        )
    else:
        formatter = logging.Formatter(
            "[{asctime}|{module:<10}:{lineno:<4}] {levelname:<8}: {message}",
            style="{",
            datefmt=CLASSIC_DATEFMT,
        )
    h.setLevel(level)
    h.setFormatter(formatter)
    h.addFilter(_strip_trailing_newlines)
    return h


def _compensate_litellm_logging() -> None:
    """
    The litellm library installs its own handlers on the "LiteLLM" logger
    and performs surprising formatting. Removing them lets our handlers
    and level settings control the output.

    This is cheap and safe to call unconditionally.
    """
    logger = logging.getLogger("LiteLLM")
    for h in logger.handlers.copy():
        logger.removeHandler(h)
        h.close()
    logger.propagate = True


def setup_logging(
    *,
    log_file: str | Path | None = None,
    level: int = logging.INFO,
    debug_modules: list[str] | None = None,
    force_color: bool | None = None,
) -> None:
    """
    Configure the root logger with a console handler (and optionally a file
    handler).

    Console output is colored when auto-detection (or force_color) says it
    should be. File output is always plain text.

    Args:
        log_file: Optional path for a plain-text log file (receives DEBUG+).
        level: Root logger level. Handlers are created at DEBUG so they emit
               whatever the per-module loggers allow.
        debug_modules: Module names that should be set to DEBUG after the
                       root configuration is in place.
        force_color: None (default) = auto (tty and not $NO_COLOR).
                     True/False forces the decision (handy for tests/CI).

    The call is safe to make twice; the second basicConfig is a no-op.
    """
    if force_color is not None:
        colored = bool(force_color)
    else:
        colored = sys.stderr.isatty() and not os.environ.get("NO_COLOR")

    handlers: list[logging.Handler] = [
        _setup_handler(logging.StreamHandler(), colored=colored)
    ]
    if log_file is not None:
        handlers.append(_setup_handler(logging.FileHandler(log_file), colored=False))

    logging.basicConfig(level=level, handlers=handlers)

    if debug_modules:
        for module in debug_modules:
            logging.getLogger(module).setLevel(logging.DEBUG)

    _compensate_litellm_logging()

    _LOGGER.debug(
        "logsetup configured (colored=%s, file=%s, debug_modules=%s)",
        colored,
        log_file,
        debug_modules,
    )


# -----------------------------------------------------------------------------
# Best Practices expected of code that uses this module
# -----------------------------------------------------------------------------
#
# 1. Call setup_logging() only from a script's `if __name__ == "__main__":`
#    block. Never at module import time.
#
# 2. Every .py file (whether library or script) does this near the top:
#       _LOGGER = logging.getLogger(__name__)
#
# 3. Prefer the logger hierarchy: bump specific modules to DEBUG rather than
#    lowering the root. Keep handlers at DEBUG so they can emit what the
#    loggers allow through.
#
# 4. Color is a console presentation concern only. Log files are always plain.
#
# 5. Fail fast on missing optional dependencies (e.g. colorlog when color is
#    requested).
#
# 6. The stripper filter and LiteLLM compensator are intentionally
#    unconditional for handlers created here. They are cheap and defensive.
#
# 7. Comments explain non-obvious choices (level interactions, history of
#    particular filters, color detection) so the next reader doesn't have
#    to rediscover them.
#
# The two format strings and the color palette above are the single source
# of truth for "how my logs look."
# -----------------------------------------------------------------------------


if __name__ == "__main__":
    # Self-test / demo when the module is run directly.
    print("=== logsetup self-demo ===")
    setup_logging(
        level=logging.DEBUG,
        debug_modules=["__main__"],
        # force_color=True,   # uncomment to force colored output
        # force_color=False,
    )

    _LOGGER.debug("DEBUG message (appears because __main__ was bumped)")
    _LOGGER.info("Normal info line")
    _LOGGER.warning("Warning")
    _LOGGER.error("Error (no traceback)")
    _LOGGER.exception("Exception with traceback")

    # Exercise the extra level exported for consumers that want it.
    _LOGGER.log(DEBUG_EXTRA, "DEBUG_EXTRA (level=%d) — usually suppressed", DEBUG_EXTRA)

    # Demonstrate the stripper filter.
    # Some libraries append literal trailing newlines to their log messages
    # (historically LiteLLM was a notable offender). The filter does
    # record.msg = record.msg.strip() so the resulting log line does not
    # produce a spurious blank line in the console or log file.
    _LOGGER.info("Normal line before the noisy one")
    _LOGGER.info("This message ends with a newline that the filter strips\n")
    print("=== demo complete ===")  # should appear immediately after the previous line, no extra blank
    print("Handlers on root:", logging.root.handlers)
