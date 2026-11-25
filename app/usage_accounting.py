"""Normalize optional provider usage without invalidating a successful generation.

Missing or malformed metadata is represented explicitly, never as verified zero cost.
These records are estimates and diagnostics, not a replacement for provider invoices.
"""
import math
import re

MAX_NUMBER = 2 ** 53 - 1
TOKEN_FIELDS = ("calls", "input", "cached_input", "output", "reasoning", "incomplete_calls")


def number(value, *, integer=False):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,16}(?:\.[0-9]{1,9})?", value):
        value = float(value) if "." in value else int(value)
    if type(value) not in (int, float) or not 0 <= value <= MAX_NUMBER or not math.isfinite(value):
        return None
    if integer and value != int(value):
        return None
    return int(value) if integer else float(value)


def field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def token_record(response):
    usage = field(response, "usage")
    incoming = field(usage, "prompt_tokens")
    outgoing = field(usage, "completion_tokens")
    cached = field(field(usage, "prompt_tokens_details"), "cached_tokens")
    if cached is None or type(cached) in (int, float) and cached == 0:
        cached = field(usage, "prompt_cache_hit_tokens", 0)
    reasoning = field(field(usage, "completion_tokens_details"), "reasoning_tokens", 0)
    if cached is None:
        cached = 0
    if reasoning is None:
        reasoning = 0
    raw = {"input": incoming, "output": outgoing, "cached_input": cached, "reasoning": reasoning}
    normalized = {k: number(v, integer=True) for k, v in raw.items()}
    incomplete = any(v is None for v in normalized.values())
    record = {k: v if v is not None else 0 for k, v in normalized.items()}
    if record["cached_input"] > record["input"] or record["reasoning"] > record["output"]:
        incomplete = True
        record["cached_input"] = min(record["cached_input"], record["input"])
        record["reasoning"] = min(record["reasoning"], record["output"])
    return {"calls": 1, **record, "incomplete_calls": int(incomplete)}


def token_totals(value):
    """Normalize older checkpoint counters without propagating malformed fields."""
    if not isinstance(value, dict):
        return {k: int(k == "incomplete_calls") for k in TOKEN_FIELDS}
    normalized = {k: number(value.get(k, 0), integer=True) for k in TOKEN_FIELDS}
    malformed = any(v is None for v in normalized.values())
    result = {k: v if v is not None else 0 for k, v in normalized.items()}
    if result["cached_input"] > result["input"] or result["reasoning"] > result["output"]:
        malformed = True
        result["cached_input"] = min(result["cached_input"], result["input"])
        result["reasoning"] = min(result["reasoning"], result["output"])
    if malformed:
        result["incomplete_calls"] = max(1, result["incomplete_calls"])
    return result


def audio_seconds(response, duration):
    """Return reported seconds or explicitly marked local audio-duration estimate."""
    try:
        usage = field(response, "usage")
        if callable(getattr(usage, "model_dump", None)):
            usage = usage.model_dump()
        seconds = number(field(usage, "seconds"))
    except Exception:
        seconds = None  # Optional SDK metadata must not discard a valid transcript.
    return (duration, True) if seconds is None else (seconds, False)


def audio_totals(value):
    value = value if isinstance(value, dict) else {}
    calls = number(value.get("asr_calls", 0), integer=True)
    seconds = number(value.get("asr_seconds", 0))
    estimates = number(value.get("asr_estimated_calls", 0), integer=True)
    invalid = any(v is None for v in (calls, seconds, estimates))
    return {"asr_calls": calls or 0, "asr_seconds": seconds or 0.0,
            "asr_estimated_calls": max(estimates or 0, int(invalid))}
