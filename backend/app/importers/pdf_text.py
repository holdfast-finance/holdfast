"""PDF -> layout text. The one place the importer touches a statement file (I/O).

Uses the sanitizer's extractor and renderer so a real statement and its sanitized fixture have the
same text shape. Needs the ``sanitize`` extra (pdfplumber). Input size is capped: a statement is a
few hundred kilobytes, and the row regexes are quadratic on very long lines.
"""

from __future__ import annotations

from pathlib import Path

from app.importers.sanitize import extract, render_text

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000


class StatementTooLargeError(ValueError):
    """The file or its text exceeds the import caps. The message carries no content."""


def pdf_to_text(path: Path) -> str:
    if path.stat().st_size > MAX_PDF_BYTES:
        raise StatementTooLargeError("pdf too large")
    text = render_text(extract(path))
    if len(text) > MAX_TEXT_CHARS:
        raise StatementTooLargeError("statement text too large")
    return text
