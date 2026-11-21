"""The secret-material guard (hard rule 11).

``reject_secret_material`` runs before any parser, validator or free-text field that can hold an
xpub, descriptor, address or label. It refuses:

- extended private keys (``xprv``, ``yprv``, ``zprv``, ``Yprv``, ``Zprv``, ``tprv``, ``uprv``,
  ``vprv``, ``Uprv``, ``Vprv``), also inside a descriptor, and any ``...prv`` + base58 body;
- WIF private keys (mainnet ``5``/``K``/``L``, testnet ``9``/``c``);
- 64-hex-digit values (a raw private key, with or without ``0x``);
- BIP39-looking word lists (English list, plus any long run of plain words in any language).

It is deliberately strict: a public key, descriptor or address never looks like any of these. The
error is a fixed string (:class:`SecretMaterialError`) and the input is never echoed, sliced or
logged.
"""

from __future__ import annotations

import re

from embit.wordlists.bip39 import WORDLIST

from app.domain.crypto.errors import CryptoInputError, SecretMaterialError

MAX_INPUT_CHARS = 4096

_BASE58 = "1-9A-HJ-NP-Za-km-z"
_XPRV = re.compile(rf"[xyztuvXYZTUV]prv[{_BASE58}]{{20,}}")
_ANY_PRV = re.compile(rf"prv[{_BASE58}]{{80,}}")
# 51 (uncompressed) or 52 (compressed) base58 characters with a WIF first character, not part of a
# longer base58 run (an xpub is 111 characters, so it never matches).
_WIF = re.compile(
    rf"(?<![{_BASE58}])(?:[59][{_BASE58}]{{50}}|[KLc][{_BASE58}]{{51}})(?![{_BASE58}])"
)
_HEX_KEY = re.compile(r"(?<![0-9A-Fa-f])(?:0[xX])?[0-9A-Fa-f]{64}(?![0-9A-Fa-f])")
_TOKEN_SPLIT = re.compile(r"[\s,;]+")
_WORDS = frozenset(WORDLIST)


def _looks_like_word_list(text: str) -> bool:
    tokens = [t for t in _TOKEN_SPLIT.split(text.strip().lower()) if t]
    if len(tokens) >= 8:
        known = sum(1 for t in tokens if t in _WORDS)
        if known * 5 >= len(tokens) * 4:  # at least 80 percent, in integers
            return True
    # A longer run of plain words, whatever the language (BIP39 has other wordlists).
    return len(tokens) >= 12 and all(t.isalpha() for t in tokens)


def contains_secret_material(value: str) -> bool:
    """True when ``value`` looks like a seed phrase or private key. Never raises."""
    return bool(
        _XPRV.search(value)
        or _ANY_PRV.search(value)
        or _WIF.search(value)
        or _HEX_KEY.search(value)
        or _looks_like_word_list(value)
    )


def reject_secret_material(value: str) -> None:
    """Raise :class:`SecretMaterialError` (fixed message) if ``value`` could hold a secret.

    Also refuses inputs longer than ``MAX_INPUT_CHARS``, with an equally fixed message.
    """
    if len(value) > MAX_INPUT_CHARS:
        raise CryptoInputError("That value is too long to be a key, descriptor or address.")
    if contains_secret_material(value):
        raise SecretMaterialError from None
