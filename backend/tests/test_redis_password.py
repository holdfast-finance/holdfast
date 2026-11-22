"""REDIS_PASSWORD reaches both Redis clients, and never shows in reprs. No live server."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.main import create_redis
from app.worker.main import MaskedRedisSettings, WorkerSettings
from tests.conftest import make_settings

PW = "pw-Abc_123"


def test_api_client_and_worker_settings_both_carry_the_password(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("REDIS_PASSWORD", PW)
    s = make_settings()
    client = create_redis(s)
    assert client.connection_pool.connection_kwargs["password"] == PW
    worker = MaskedRedisSettings.from_dsn(s.redis_dsn)
    assert worker.password == PW
    assert worker.host == "redis"


def test_worker_redis_settings_repr_masks_the_password(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("REDIS_PASSWORD", PW)
    worker = MaskedRedisSettings.from_dsn(make_settings().redis_dsn)
    assert PW not in repr(worker)
    assert "***" in repr(worker)
    assert isinstance(WorkerSettings.redis_settings, MaskedRedisSettings)
    assert "password" not in repr(WorkerSettings.redis_settings) or PW not in repr(
        WorkerSettings.redis_settings
    )


@pytest.mark.parametrize("bad", ["a@b", "p/w", "x:y", "100%", "sp ace", "ü"])
def test_password_with_reserved_characters_is_rejected(
    clean_env: pytest.MonkeyPatch, bad: str
) -> None:
    clean_env.setenv("REDIS_PASSWORD", bad)
    with pytest.raises(ValidationError, match="REDIS_PASSWORD may only contain"):
        make_settings()
