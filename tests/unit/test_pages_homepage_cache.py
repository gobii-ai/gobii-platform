from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, tag
from django.utils import timezone

from pages.homepage_cache import (
    HOMEPAGE_PRETRAINED_CACHE_FRESH_SECONDS,
    HOMEPAGE_PRETRAINED_CACHE_STALE_SECONDS,
    _homepage_pretrained_cache_key,
    get_homepage_pretrained_payload,
)


@tag("batch_pages")
class HomepagePretrainedCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("pages.homepage_cache._build_homepage_pretrained_payload")
    @patch("pages.homepage_cache._enqueue_homepage_pretrained_refresh")
    def test_cache_miss_populates_cache(self, mock_enqueue, mock_build):
        mock_build.return_value = {"templates": [], "categories": [], "total": 0}

        result = get_homepage_pretrained_payload()

        self.assertEqual(result, mock_build.return_value)
        mock_enqueue.assert_not_called()

        cache_entry = cache.get(_homepage_pretrained_cache_key())
        self.assertIsNotNone(cache_entry)
        self.assertEqual(cache_entry["data"], mock_build.return_value)

    @patch("pages.homepage_cache._build_homepage_pretrained_payload")
    @patch("pages.homepage_cache._enqueue_homepage_pretrained_refresh")
    def test_fresh_cache_hit_skips_refresh(self, mock_enqueue, mock_build):
        cached_data = {"templates": [{"code": "demo"}], "categories": [], "total": 1}
        cache.set(
            _homepage_pretrained_cache_key(),
            {"data": cached_data, "refreshed_at": timezone.now().timestamp()},
            timeout=HOMEPAGE_PRETRAINED_CACHE_STALE_SECONDS,
        )

        result = get_homepage_pretrained_payload()

        self.assertEqual(result, cached_data)
        mock_build.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("pages.homepage_cache._build_homepage_pretrained_payload")
    @patch("pages.homepage_cache._enqueue_homepage_pretrained_refresh")
    def test_stale_cache_triggers_refresh(self, mock_enqueue, mock_build):
        self.assertGreater(
            HOMEPAGE_PRETRAINED_CACHE_STALE_SECONDS,
            HOMEPAGE_PRETRAINED_CACHE_FRESH_SECONDS,
        )
        cached_data = {"templates": [{"code": "demo"}], "categories": [], "total": 1}
        cache.set(
            _homepage_pretrained_cache_key(),
            {
                "data": cached_data,
                "refreshed_at": timezone.now().timestamp()
                - (HOMEPAGE_PRETRAINED_CACHE_FRESH_SECONDS + 5),
            },
            timeout=HOMEPAGE_PRETRAINED_CACHE_STALE_SECONDS,
        )

        result = get_homepage_pretrained_payload()

        self.assertEqual(result, cached_data)
        mock_build.assert_not_called()
        mock_enqueue.assert_called_once()
