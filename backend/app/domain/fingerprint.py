"""Import fingerprints: a stable 64-char hex id for one imported transaction (PLAN 8.1 step 5).

The database keeps ``UNIQUE (account_id, import_fingerprint)``, so the account is not part of the
hash. Two identical rows in one statement (same day, amount and description) are different
purchases, so each carries a 1-based same-day ordinal: the n-th occurrence gets ordinal n. The same
statement re-imported (or the same row inside an overlapping statement, in the same order) hashes to
the same value and inserts nothing.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

from app.domain.money import quantize_cents

FINGERPRINT_VERSION = "v1"


def normalize_description(description: str) -> str:
    """Collapse whitespace and fold case so layout and capitalization drift do not split a row."""
    return " ".join(description.split()).upper()


def import_fingerprint(posted: date, amount: Decimal, description: str, ordinal: int) -> str:
    if ordinal < 1:
        raise ValueError("ordinal is 1-based")
    parts = (
        FINGERPRINT_VERSION,
        posted.isoformat(),
        str(quantize_cents(amount)),
        normalize_description(description),
        str(ordinal),
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
