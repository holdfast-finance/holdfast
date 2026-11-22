"""Stable merchant key derived from a transaction's merchant name or raw description.

Pure string handling (no ``re``). Bump MERCHANT_KEY_VERSION when the algorithm
changes so stored rules are flagged instead of silently matching differently.
"""

from __future__ import annotations

import unicodedata

from app.domain.fingerprint import normalize_description

MERCHANT_KEY_VERSION = 1

# Generic processor words only. Never an institution or a person (hard rule 13).
NOISE_TOKENS = ("POS", "DEBIT", "PURCHASE", "CHECKCARD", "RECURRING", "ACH", "SQ", "TST")

MAX_TOKENS = 4
MAX_KEY_LENGTH = 64
MIN_KEY_LENGTH = 2
_SEPARATORS = str.maketrans({c: " " for c in "*#/\\|:;,"})


def _normalize(text: str) -> str:
    """NFKC first so width and compatibility forms can't split one merchant into two keys."""
    return normalize_description(unicodedata.normalize("NFKC", text))


def merchant_key(merchant_name: str | None, description_raw: str) -> str | None:
    source = _normalize(merchant_name) if merchant_name else ""
    if not any(ch.isalpha() for ch in source):
        source = _normalize(description_raw)
    tokens = [
        t
        for t in source.translate(_SEPARATORS).split()
        if not any(ch.isdigit() or ch.isnumeric() for ch in t)
    ]
    start = 0
    while start < len(tokens) and tokens[start] in NOISE_TOKENS:
        start += 1
    key = " ".join(tokens[start : start + MAX_TOKENS])[:MAX_KEY_LENGTH].strip()
    return key if len(key) >= MIN_KEY_LENGTH else None
