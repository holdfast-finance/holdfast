"""Load bank profiles (``profiles/*.yaml``) into ``Profile`` objects. Filesystem I/O lives here."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.domain.csv_parse import CsvProfile, csv_profile_from_mapping
from app.domain.statement_parse import Profile, StatementParseError, profile_from_mapping

PROFILE_DIR = Path(__file__).parent / "profiles"
CSV_PROFILE_DIR = Path(__file__).parent / "csv_profiles"


def load_profiles(directory: Path = PROFILE_DIR) -> list[Profile]:
    profiles: list[Profile] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise StatementParseError(f"{path.name}: profile must be a mapping")
        profiles.append(profile_from_mapping(data))
    ids = [p.id for p in profiles]
    if len(ids) != len(set(ids)):
        raise StatementParseError("duplicate profile id")
    return profiles


def load_csv_profiles(directory: Path = CSV_PROFILE_DIR) -> list[tuple[str, CsvProfile]]:
    """CSV column mappings as ``(profile id, profile)``; the id is ``csv-<file stem>``."""
    found: list[tuple[str, CsvProfile]] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise StatementParseError(f"{path.name}: profile must be a mapping")
        found.append((f"csv-{path.stem.lower()}", csv_profile_from_mapping(data)))
    return found
