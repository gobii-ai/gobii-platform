from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, tag
from django.utils import timezone

from console.home_metrics import (
    CONSOLE_HOME_CACHE_FRESH_SECONDS,
    CONSOLE_HOME_CACHE_STALE_SECONDS,
    _console_home_cache_key,
    get_console_home_metrics,
)

User = get_user_model()


@tag("batch_console_agents")
class ConsoleHomeMetricsCacheTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="console-home-cache",
            email="console-home-cache@example.com",
            password="pw",
        )
        self.factory = RequestFactory()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _request(self):
        request = self.factory.get("/console")
        request.user = self.user
        return request

    @patch("console.home_metrics._build_console_home_metrics_for_owner")
    @patch("console.home_metrics._enqueue_console_home_refresh")
    def test_cache_miss_populates_cache(self, mock_enqueue, mock_build):
        mock_build.return_value = {"agent_count": 1}

        result = get_console_home_metrics(
            self._request(),
            {"type": "personal", "id": str(self.user.id)},
            None,
        )

        self.assertEqual(result, mock_build.return_value)
        mock_enqueue.assert_not_called()

        cache_entry = cache.get(_console_home_cache_key("user", self.user.id))
        self.assertIsNotNone(cache_entry)
        self.assertEqual(cache_entry["data"], mock_build.return_value)

    @patch("console.home_metrics._build_console_home_metrics_for_owner")
    @patch("console.home_metrics._enqueue_console_home_refresh")
    def test_fresh_cache_hit_skips_refresh(self, mock_enqueue, mock_build):
        cached_data = {"agent_count": 2}
        cache.set(
            _console_home_cache_key("user", self.user.id),
            {"data": cached_data, "refreshed_at": timezone.now().timestamp()},
            timeout=CONSOLE_HOME_CACHE_STALE_SECONDS,
        )

        result = get_console_home_metrics(
            self._request(),
            {"type": "personal", "id": str(self.user.id)},
            None,
        )

        self.assertEqual(result, cached_data)
        mock_build.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("console.home_metrics._build_console_home_metrics_for_owner")
    @patch("console.home_metrics._enqueue_console_home_refresh")
    def test_stale_cache_triggers_refresh(self, mock_enqueue, mock_build):
        self.assertGreater(
            CONSOLE_HOME_CACHE_STALE_SECONDS,
            CONSOLE_HOME_CACHE_FRESH_SECONDS,
        )
        cached_data = {"agent_count": 3}
        cache.set(
            _console_home_cache_key("user", self.user.id),
            {
                "data": cached_data,
                "refreshed_at": timezone.now().timestamp()
                - (CONSOLE_HOME_CACHE_FRESH_SECONDS + 5),
            },
            timeout=CONSOLE_HOME_CACHE_STALE_SECONDS,
        )

        result = get_console_home_metrics(
            self._request(),
            {"type": "personal", "id": str(self.user.id)},
            None,
        )

        self.assertEqual(result, cached_data)
        mock_build.assert_not_called()
        mock_enqueue.assert_called_once_with("user", self.user.id)
