"""Decide what an uploaded file is from its bytes (never from its name or the client's MIME type).

Pure: no I/O. The upload route sniffs the first chunk with ``detect_file_kind`` and then checks
every later chunk of a text file with ``has_binary_bytes`` so a binary file cannot pass as CSV.
"""

import codecs
import re
from typing import Literal

FileKind = Literal["pdf", "ofx", "csv"]

PDF_MAGIC = b"%PDF-"
_WIDE_BOMS = (
    codecs.BOM_UTF32_LE,  # before UTF-16 LE: its BOM starts with the same two bytes
    codecs.BOM_UTF32_BE,
    codecs.BOM_UTF16_LE,
    codecs.BOM_UTF16_BE,
)
# Control bytes that never appear in text (tab, LF, FF and CR are allowed).
_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0e-\x1f\x7f]")
_CONTROL_TEXT = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
# OFX 1.x starts with an OFXHEADER block, OFX 2.x with an <?OFX?> instruction after the XML
# declaration, and a bare SGML body with <OFX>. Anchored, so a CSV that merely mentions "<OFX>"
# in a description is still a CSV.
_OFX_START = re.compile(
    r"\A(?:<\?xml[^>]{0,200}\?>\s*)?(?:<\?OFX\b|<OFX\s*>|OFXHEADER\s*:)", re.IGNORECASE
)
_DELIMITERS = (",", ";", "\t", "|")


def is_wide_text(head: bytes) -> bool:
    """True for a UTF-16 or UTF-32 file with a byte order mark (its NUL bytes are legitimate)."""
    return head.startswith(_WIDE_BOMS)


def has_binary_bytes(chunk: bytes) -> bool:
    """True if a (non-UTF-16/32) text chunk holds a NUL or other control byte."""
    return _CONTROL.search(chunk) is not None


def _decode_head(head: bytes) -> str | None:
    """The head as text, or ``None`` if it is binary. A cut-off final character is tolerated."""
    if is_wide_text(head):
        codec = "utf-32" if head.startswith(_WIDE_BOMS[:2]) else "utf-16"
        text = head.decode(codec, errors="ignore")
        return None if _CONTROL_TEXT.search(text) else text
    if has_binary_bytes(head):
        return None
    return head.decode("utf-8", errors="replace").removeprefix("﻿")


def detect_file_kind(head: bytes) -> FileKind | None:
    """``pdf``, ``ofx`` (OFX or QFX), ``csv`` (any delimited text), or ``None`` (not importable)."""
    if head.startswith(PDF_MAGIC):
        return "pdf"
    text = _decode_head(head)
    if text is None:
        return None
    text = text.lstrip()
    if _OFX_START.match(text):
        return "ofx"
    if text and any(d in text for d in _DELIMITERS):
        return "csv"
    return None


class WideTextGuard:
    """Checks a UTF-16/32 upload chunk by chunk: it must decode and hold no control characters.

    An incremental decoder keeps a character split across two chunks whole. ``feed`` returns
    False for a chunk that does not decode or holds a control character; ``finish`` for a
    truncated final character.
    """

    def __init__(self, head: bytes) -> None:
        codec = "utf-32" if head.startswith(_WIDE_BOMS[:2]) else "utf-16"
        self._decoder = codecs.getincrementaldecoder(codec)(errors="strict")

    def feed(self, chunk: bytes) -> bool:
        try:
            return _CONTROL_TEXT.search(self._decoder.decode(chunk)) is None
        except UnicodeError:
            return False

    def finish(self) -> bool:
        try:
            return _CONTROL_TEXT.search(self._decoder.decode(b"", final=True)) is None
        except UnicodeError:
            return False
