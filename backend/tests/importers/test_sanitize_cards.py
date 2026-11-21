"""Card-style layouts: month-name dates, parenthesised credits, CR markers."""

from __future__ import annotations

import re
from pathlib import Path

from reportlab.pdfgen import canvas

from app.importers import sanitize as sz


def test_card_style_month_names_parentheses_and_cr_keep_reconciliation(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    c = canvas.Canvas(str(src / "card.pdf"))
    rows = [
        ("Previous Balance", "$500.00"),
        ("Jan 5 ZEBRATRONIX", "$125.40"),
        ("Jan 9 Payment Thank You", "($300.00)"),
        ("Jan 12 Refund Marigold", "$20.00 CR"),
        ("New Balance", "$305.40"),
    ]
    for i, (left, right) in enumerate(rows):
        c.drawString(50, 740 - 16 * i, f"{left} {right}")
    c.save()
    out = tmp_path / "out"
    summary, _ = sz.sanitize_path(src, out, seed=11)
    rep = summary["docs"][0]
    assert rep["reconciled_before"] is True
    assert rep["reconciled_after"] is True
    blob = (out / "doc-0001.txt").read_text()
    assert "ZEBRATRONIX" not in blob and "Jan 5" not in blob
    assert "CR" in blob
    assert re.search(r"\(\$[\d,]+\.\d{2}\)", blob)
