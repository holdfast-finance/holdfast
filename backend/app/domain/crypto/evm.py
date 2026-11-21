"""EVM addresses: EIP-55 checksum validation and normalisation (ADR 0020).

An address is public data. The guard still runs first: a 64-hex string (a raw private key, with or
without ``0x``) is refused with a fixed message and never echoed. ENS names and bare 40-hex strings
without ``0x`` are not accepted.
"""

from __future__ import annotations

import re

from Crypto.Hash import keccak

from app.domain.crypto.errors import CryptoInputError
from app.domain.crypto.guard import reject_secret_material

_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")


def keccak256(data: bytes) -> bytes:
    """Keccak-256 (the pre-NIST variant Ethereum uses, not ``hashlib.sha3_256``)."""
    return keccak.new(digest_bits=256, data=data).digest()


def to_checksum_address(address: str) -> str:
    """The EIP-55 mixed-case spelling of a 40-hex address (``0x`` prefix required)."""
    if _ADDRESS.fullmatch(address) is None:
        raise CryptoInputError("That is not a valid EVM address.")
    lower = address[2:].lower()
    digest = keccak256(lower.encode("ascii")).hex()
    return "0x" + "".join(c.upper() if int(digest[i], 16) >= 8 else c for i, c in enumerate(lower))


def normalise_evm_address(text: str) -> str:
    """Validate an EVM address and return its EIP-55 checksummed spelling.

    All-lower-case and all-upper-case hex carry no checksum and are accepted; a mixed-case address
    must match its checksum exactly, because a wrong case usually means a mistyped character.
    """
    reject_secret_material(text)
    candidate = text.strip()
    if _ADDRESS.fullmatch(candidate) is None:
        raise CryptoInputError("That is not a valid EVM address.")
    digits = candidate[2:]
    checksummed = to_checksum_address(candidate)
    if digits not in (digits.lower(), digits.upper()) and checksummed != candidate:
        raise CryptoInputError(
            "That EVM address has a wrong checksum. Check it was copied in full."
        )
    return checksummed


def address_key(address: str) -> str:
    """The lower-case form used to compare or look up an address (case carries no meaning)."""
    return normalise_evm_address(address).lower()
