"""Domain test fixtures."""

from collections.abc import Iterator

import pytest

from app.domain import rule_regex


@pytest.fixture(autouse=True)
def lenient_probe(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Make acceptance tests independent of machine load.

    The probe times real searches, so a busy runner can push a benign pattern over 10 us.
    Acceptance is not a timing property, so tests that only need a pattern to be accepted run
    with a limit 10x higher. Tests that assert the probe's own decision carry
    ``@pytest.mark.strict_probe``: those patterns are 10x or more over the limit, and load only
    makes them slower, so they cannot flake toward acceptance.
    """
    if request.node.get_closest_marker("strict_probe") is None:
        monkeypatch.setattr(
            rule_regex,
            "PROBE_LIMIT_MICROSECONDS",
            rule_regex.PROBE_LIMIT_MICROSECONDS * 10,
        )
    yield
