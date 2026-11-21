"""Input errors for crypto parsers. Messages are fixed strings and never echo the input.

The input may be a pasted seed phrase or private key, so nothing derived from it (not the text, a
slice or a length) goes into an exception message, a log line or a validation error.
"""

from __future__ import annotations


class CryptoInputError(ValueError):
    """A value is not an acceptable public key, descriptor or address."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SecretMaterialError(CryptoInputError):
    """The value looks like a seed phrase or private key. It was not stored or logged."""

    MESSAGE = (
        "That looks like a seed phrase or a private key. Holdfast only takes public data "
        "(an xpub, an output descriptor or an address) and never stores or sends secrets."
    )

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)
