"""
log_usage.py — OpenRouter usage response logger.

Resolves the caller's module-level _LOGGER via frame inspection.
Logs a compact one-line summary at INFO on a clean response,
escalates to WARNING on any structural or value anomaly.
"""

import inspect
import logging

# ── Expected schema ────────────────────────────────────────────────────────────

_TOP_LEVEL_REQUIRED = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
    "is_byok",
    "prompt_tokens_details",
    "cost_details",
    "completion_tokens_details",
}

_PROMPT_DETAIL_KEYS = {"cached_tokens", "cache_write_tokens", "audio_tokens", "video_tokens"}
_COST_DETAIL_KEYS = {
    "upstream_inference_cost",
    "upstream_inference_prompt_cost",
    "upstream_inference_completions_cost",
}
_COMPLETION_DETAIL_KEYS = {"reasoning_tokens", "image_tokens", "audio_tokens"}

# Reasoning tokens are a sub-breakdown of completion_tokens, not additive.
# Reconciliation: prompt_tokens + completion_tokens == total_tokens.


# ── Frame inspection ───────────────────────────────────────────────────────────

def _resolve_logger() -> tuple[logging.Logger, bool]:
    """
    Walk up the call stack to find the caller's module-level _LOGGER.

    Returns (logger, force_warn):
      - force_warn=True means no _LOGGER was found; all output escalates to WARNING.
    """
    stack = inspect.stack()
    # stack[0] = _resolve_logger, [1] = log_usage, [2] = direct caller, [3+] = further up
    for frame_info in stack[2:]:
        g = frame_info[0].f_globals
        candidate = g.get("_LOGGER")
        if isinstance(candidate, logging.Logger):
            return candidate, False

    # No _LOGGER anywhere in the call chain — that is itself the anomaly.
    return logging.getLogger(__name__), True


# ── Validation helpers ─────────────────────────────────────────────────────────

def _check_detail_block(
    usage: dict,
    key: str,
    expected_keys: set[str],
    warnings: list[str],
) -> dict:
    """
    Validate a nested detail dict. Returns the block (or {}) and appends
    any issues to *warnings*.

    NOTE: modifies the WARNINGS parameter, intentionally as an accumulation.
    """
    block = usage.get(key)
    if block is None:
        warnings.append(f"missing nested block '{key}'")
        return {}
    if not isinstance(block, dict):
        warnings.append(f"'{key}' is not a dict (got {type(block).__name__})")
        return {}
    unknown = set(block) - expected_keys
    if unknown:
        warnings.append(f"unknown keys in '{key}': {sorted(unknown)}")
    for k, v in block.items():
        if isinstance(v, (int, float)) and v < 0:
            warnings.append(f"negative value in '{key}': {k}={v}")
    return block


# ── Format helpers ─────────────────────────────────────────────────────────────

def _fmt_cost(cost: float) -> str:
    """Four significant figures, dollar-prefixed."""
    return f"${cost:.4g}"


def _nonzero_parts(mapping: dict, keys: list[str]) -> list[str]:
    """Return 'key=value' strings for non-zero numeric values, in key order."""
    parts = []
    for k in keys:
        v = mapping.get(k, 0)
        if isinstance(v, (int, float)) and v:
            parts.append(f"{k}={v:,}")
    return parts


# ── Public entry point ─────────────────────────────────────────────────────────

def log_usage(usage: dict) -> None:
    """
    Parse and log an OpenRouter usage dict.

    Resolves _LOGGER from the caller's module globals.  Logs at INFO when
    the response matches the expected schema and values; escalates to WARNING
    on any structural, value, or schema anomaly.
    """
    logger, force_warn = _resolve_logger()
    warnings: list[str] = []

    if force_warn:
        warnings.append("no module-level _LOGGER found in call chain")

    # ── Structural checks ──────────────────────────────────────────────────────

    if not isinstance(usage, dict):
        msg = f"OpenRouter usage — unexpected type: {type(usage).__name__}: {usage!r}"
        logger.warning(msg)
        return

    missing = _TOP_LEVEL_REQUIRED - set(usage)
    if missing:
        warnings.append(f"missing top-level keys: {sorted(missing)}")

    unknown = set(usage) - _TOP_LEVEL_REQUIRED
    if unknown:
        warnings.append(f"unknown top-level keys: {sorted(unknown)} — schema may have changed")

    # ── Nested detail blocks ───────────────────────────────────────────────────

    prompt_detail = _check_detail_block(usage, "prompt_tokens_details", _PROMPT_DETAIL_KEYS, warnings)
    cost_detail = _check_detail_block(usage, "cost_details", _COST_DETAIL_KEYS, warnings)
    completion_detail = _check_detail_block(usage, "completion_tokens_details", _COMPLETION_DETAIL_KEYS, warnings)

    # ── Value checks ───────────────────────────────────────────────────────────

    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    total_tokens = usage.get("total_tokens", 0)
    cost = usage.get("cost", 0.0)

    if total_tokens == 0:
        warnings.append("total_tokens is zero")

    if isinstance(cost, (int, float)) and cost < 0:
        warnings.append(f"negative cost: {cost}")

    # Token reconciliation (reasoning_tokens are a completion sub-breakdown, not additive)
    if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int) and isinstance(total_tokens, int):
        if prompt_tokens + completion_tokens != total_tokens:
            warnings.append(
                f"token reconciliation failed: "
                f"{prompt_tokens} prompt + {completion_tokens} completion "
                f"= {prompt_tokens + completion_tokens}, not {total_tokens}"
            )

    if usage.get("is_byok"):
        warnings.append("is_byok=True — unexpected; this code does not use BYOK routing")

    # ── Build the one-line summary ─────────────────────────────────────────────

    prompt_extras = _nonzero_parts(
        prompt_detail,
        ["cached_tokens", "cache_write_tokens", "audio_tokens", "video_tokens"],
    )
    completion_extras = _nonzero_parts(
        completion_detail,
        ["reasoning_tokens", "image_tokens", "audio_tokens"],
    )

    prompt_str = f"{prompt_tokens:,}"
    if prompt_extras:
        prompt_str += f" ({', '.join(prompt_extras)})"

    completion_str = f"{completion_tokens:,}"
    if completion_extras:
        completion_str += f" ({', '.join(completion_extras)})"

    summary = (
        f"OpenRouter usage — "
        f"{prompt_str} prompt + {completion_str} completion "
        f"= {total_tokens:,} tokens | {_fmt_cost(cost)}"
    )

    # ── Emit ───────────────────────────────────────────────────────────────────

    if warnings:
        logger.warning("%s | WARNINGS: %s", summary, "; ".join(warnings))
    else:
        logger.info(summary)
