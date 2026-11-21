"""The PDF reader must be installed wherever the app runs, not only with an extra."""

import tomllib
from pathlib import Path


def test_pdf_reader_is_a_runtime_dependency() -> None:
    project = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())["project"]
    assert any(dep.startswith("pdfplumber") for dep in project["dependencies"])
    assert "sanitize" not in project.get("optional-dependencies", {})


def test_pdf_text_imports_without_extras() -> None:
    from app.importers import pdf_text

    assert callable(pdf_text.pdf_to_text)
