"""Load bank profiles (``profiles/*.yaml``) into ``Profile`` objects. Filesystem I/O lives here."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.domain.statement_parse import Profile, StatementParseError, profile_from_mapping

PROFILE_DIR = Path(__file__).parent / "profiles"


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
