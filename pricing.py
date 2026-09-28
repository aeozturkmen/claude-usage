"""
pricing.py - Anthropic API pricing (USD per 1M tokens) shared by the CLI and dashboard.

Source: https://claude.com/pricing#api (as of September 2026).
Cache writes: 5-minute TTL = 1.25x input, 1-hour TTL = 2x input.
Cache reads are ~0.1x input, except where Anthropic lists a special rate
(Fable 5.1: $0.25, Opus 5.5: $0.20).
Fast mode (usage.speed == "fast") multiplies every token rate by `fast_multiplier`.
Web search is billed per request on top of tokens.
"""

import hashlib
import json
import re

WEB_SEARCH_COST_PER_REQUEST = 10.00 / 1000   # $10 per 1,000 searches


def _p(inp, out, cache_read=None, fast_multiplier=1):
    return {
        "input": inp,
        "output": out,
        "cache_read": round(inp * 0.1, 4) if cache_read is None else cache_read,
        "cache_write": round(inp * 1.25, 4),      # 5-minute TTL
        "cache_write_1h": round(inp * 2.0, 4),    # 1-hour TTL
        "fast_multiplier": fast_multiplier,
    }


PRICING = {
    # Fable / Mythos tier
    "claude-fable-5-1":  _p(10.00, 50.00, cache_read=0.25),
    "claude-fable-5":    _p(10.00, 50.00),
    "claude-mythos-5-1": _p(10.00, 50.00),   # cache-read discount not confirmed; priced at 0.1x
    "claude-mythos-5":   _p(10.00, 50.00),
    # Opus
    "claude-opus-5-5":   _p(4.00, 20.00, cache_read=0.20, fast_multiplier=2),
    "claude-opus-5":     _p(5.00, 25.00, fast_multiplier=2),
    "claude-opus-4-8":   _p(5.00, 25.00, fast_multiplier=2),
    "claude-opus-4-7":   _p(5.00, 25.00, fast_multiplier=6),
    "claude-opus-4-6":   _p(5.00, 25.00, fast_multiplier=6),
    "claude-opus-4-5":   _p(5.00, 25.00),
    "claude-opus-4-1":   _p(15.00, 75.00),
    "claude-opus-4":     _p(15.00, 75.00),
    # Sonnet
    "claude-sonnet-5":   _p(2.00, 10.00),
    "claude-sonnet-4-6": _p(3.00, 15.00),
    "claude-sonnet-4-5": _p(3.00, 15.00),
    "claude-sonnet-4":   _p(3.00, 15.00),
    "claude-3-7-sonnet": _p(3.00, 15.00),
    # Haiku
    "claude-haiku-4-5":  _p(1.00, 5.00),
    "claude-3-5-haiku":  _p(0.80, 4.00),
    "claude-3-haiku":    _p(0.25, 1.25, cache_read=0.03),
}

# Unknown/future model names fall back to the newest known model of their family.
FAMILY_FALLBACK = [
    ("fable",  "claude-fable-5-1"),
    ("mythos", "claude-mythos-5-1"),
    ("opus",   "claude-opus-5"),
    ("sonnet", "claude-sonnet-5"),
    ("haiku",  "claude-haiku-4-5"),
]

# Sorted longest-first so "claude-opus-5-5[1m]" matches opus-5-5, not opus-5.
_KEYS_BY_LENGTH = sorted(PRICING, key=len, reverse=True)


def normalize_model(model):
    """Strip context-window suffixes such as '[1m]' from a model id."""
    return re.sub(r"\[[^\]]*\]$", "", model or "").strip()


def _prefix_matches(base, key):
    """True for dated snapshots / variants of `key` (claude-opus-4-5-20251101,
    claude-opus-4-6-preview), false for a different minor version
    (claude-opus-4 must not swallow claude-opus-4-9). '-0' is the x.0 alias."""
    if not base.startswith(key):
        return False
    rest = base[len(key):]
    if not rest:
        return True
    if rest[0] not in "-@":
        return False
    head = rest[1:].split("-")[0]
    return not (head.isdigit() and len(head) <= 2 and head != "0")


def get_pricing(model):
    if not model:
        return None
    base = normalize_model(model)
    if base in PRICING:
        return PRICING[base]
    for key in _KEYS_BY_LENGTH:
        if _prefix_matches(base, key):
            return PRICING[key]
    m = base.lower()
    for keyword, key in FAMILY_FALLBACK:
        if keyword in m:
            return PRICING[key]
    return None


def calc_cost(model, inp, out, cache_read, cache_creation, cache_creation_1h=0,
              speed=None, web_search_requests=0):
    """Cost in USD. cache_creation is the total cache write; cache_creation_1h
    is the portion of it written with the 1-hour TTL (billed at 2x input)."""
    p = get_pricing(model)
    if not p:
        return 0.0
    cache_1h = min(cache_creation_1h or 0, cache_creation)
    cache_5m = cache_creation - cache_1h
    tokens = (
        inp        * p["input"]          / 1_000_000 +
        out        * p["output"]         / 1_000_000 +
        cache_read * p["cache_read"]     / 1_000_000 +
        cache_5m   * p["cache_write"]    / 1_000_000 +
        cache_1h   * p["cache_write_1h"] / 1_000_000
    )
    if speed == "fast":
        tokens *= p.get("fast_multiplier", 1)
    return tokens + (web_search_requests or 0) * WEB_SEARCH_COST_PER_REQUEST


def pricing_fingerprint():
    """Changes whenever the pricing rules change; stored costs are recomputed then."""
    blob = json.dumps([PRICING, FAMILY_FALLBACK, WEB_SEARCH_COST_PER_REQUEST], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]
