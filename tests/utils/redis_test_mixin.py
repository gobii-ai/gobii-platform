"""Shared helpers for isolating Redis dependencies during unit tests."""

from __future__ import annotations

from unittest.mock import patch

from config import redis_client as redis_client_module
from tests.mocks.fake_redis import FakeRedis


class _DummyCeleryConnection:
    """Context manager stub that mimics Celery's connection API."""

    def __enter__(self):  # noqa: D401 - trivial context manager
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: D401 - trivial context manager
        return False


class RedisIsolationMixin:
    """Provide an in-memory Redis stub and silence Celery beat side effects."""

    fake_redis: FakeRedis
    _redis_patchers: list

    @classmethod
    def setUpClass(cls):  # noqa: D401 - Django hook
        super().setUpClass()
        redis_client_module.get_redis_client.cache_clear()

        cls.fake_redis = FakeRedis()
        cls._redis_patchers = [
            patch(
                "config.redis_client.get_redis_client",
                side_effect=lambda *args, **kwargs: cls.fake_redis,
            ),
            patch(
                "api.agent.events.get_redis_client",
                side_effect=lambda *args, **kwargs: cls.fake_redis,
            ),
            patch(
                "api.agent.core.event_processing.get_redis_client",
                side_effect=lambda *args, **kwargs: cls.fake_redis,
            ),
            patch(
                "api.agent.core.budget.get_redis_client",
                side_effect=lambda *args, **kwargs: cls.fake_redis,
            ),
        ]
        for patcher in cls._redis_patchers:
            patcher.start()

        cls._celery_connection_patcher = patch(
            "celery.app.base.Celery.connection",
            return_value=_DummyCeleryConnection(),
        )
        cls._celery_connection_patcher.start()

        cls._redbeat_patcher = patch("redbeat.RedBeatSchedulerEntry")
        cls._redbeat_patcher.start()

    @classmethod
    def tearDownClass(cls):  # noqa: D401 - Django hook
        cls._redbeat_patcher.stop()
        cls._celery_connection_patcher.stop()
        for patcher in cls._redis_patchers:
            patcher.stop()
        redis_client_module.get_redis_client.cache_clear()
        super().tearDownClass()

    def setUp(self):  # noqa: D401 - Django hook
        self.__class__.fake_redis = FakeRedis()
        self.fake_redis = self.__class__.fake_redis
        super().setUp()

