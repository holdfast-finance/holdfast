"""PDF -> layout text. The one place the importer touches a statement file (I/O).

Uses the sanitizer's extractor and renderer so a real statement and its sanitized fixture have the
same text shape. Needs the ``sanitize`` extra (pdfplumber).
"""

from __future__ import annotations

from pathlib import Path

from app.importers.sanitize import extract, render_text


def pdf_to_text(path: Path) -> str:
    return render_text(extract(path))
