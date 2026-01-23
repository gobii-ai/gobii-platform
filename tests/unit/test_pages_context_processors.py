from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, tag
from django.utils import timezone

from pages.context_processors import (
    ACCOUNT_INFO_CACHE_FRESH_SECONDS,
    ACCOUNT_INFO_CACHE_STALE_SECONDS,
    _account_info_cache_key,
    account_info,
)

User = get_user_model()


@tag("batch_pages")
class AccountInfoCacheTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="cache-user",
            email="cache@example.com",
            password="pw",
        )
        self.factory = RequestFactory()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _request(self):
        request = self.factory.get("/account")
        request.user = self.user
        return request

    @patch("pages.context_processors._build_account_info")
    @patch("pages.context_processors._enqueue_account_info_refresh")
    def test_cache_miss_populates_cache(self, mock_enqueue, mock_build):
        mock_build.return_value = {"account": {"paid": False}}

        result = account_info(self._request())

        self.assertEqual(result, mock_build.return_value)
        mock_enqueue.assert_not_called()

        cache_entry = cache.get(_account_info_cache_key(self.user.id))
        self.assertIsNotNone(cache_entry)
        self.assertEqual(cache_entry["data"], mock_build.return_value)

    @patch("pages.context_processors._build_account_info")
    @patch("pages.context_processors._enqueue_account_info_refresh")
    def test_fresh_cache_hit_skips_refresh(self, mock_enqueue, mock_build):
        cached_data = {"account": {"paid": True}}
        cache.set(
            _account_info_cache_key(self.user.id),
            {"data": cached_data, "refreshed_at": timezone.now().timestamp()},
            timeout=ACCOUNT_INFO_CACHE_STALE_SECONDS,
        )

        result = account_info(self._request())

        self.assertEqual(result, cached_data)
        mock_build.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("pages.context_processors._build_account_info")
    @patch("pages.context_processors._enqueue_account_info_refresh")
    def test_stale_cache_triggers_refresh(self, mock_enqueue, mock_build):
        self.assertGreater(
            ACCOUNT_INFO_CACHE_STALE_SECONDS,
            ACCOUNT_INFO_CACHE_FRESH_SECONDS,
        )
        cached_data = {"account": {"paid": True}}
        cache.set(
            _account_info_cache_key(self.user.id),
            {
                "data": cached_data,
                "refreshed_at": timezone.now().timestamp()
                - (ACCOUNT_INFO_CACHE_FRESH_SECONDS + 5),
            },
            timeout=ACCOUNT_INFO_CACHE_STALE_SECONDS,
        )

        result = account_info(self._request())

        self.assertEqual(result, cached_data)
        mock_build.assert_not_called()
        mock_enqueue.assert_called_once_with(self.user.id)
