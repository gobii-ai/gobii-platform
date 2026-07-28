from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.models import MCPServerConfig, PersistentAgentTemplate
from pages.homepage_cache import (
    HOMEPAGE_INTEGRATIONS_CACHE_FRESH_SECONDS,
    HOMEPAGE_INTEGRATIONS_CACHE_STALE_SECONDS,
    HOMEPAGE_PRETRAINED_CACHE_FRESH_SECONDS,
    HOMEPAGE_PRETRAINED_CACHE_STALE_SECONDS,
    _build_homepage_integrations_payload,
    _homepage_integrations_cache_key,
    _homepage_pretrained_cache_lock_key,
    _homepage_pretrained_cache_key,
    _serialize_template,
    get_homepage_integrations_payload,
    get_homepage_pretrained_payload,
)
from pages.library_views import (
    LIBRARY_CACHE_KEY,
    LIBRARY_CATEGORY_SLUG_MAP_CACHE_KEY,
    LIBRARY_OFFICIAL_CACHE_KEY,
)


@tag("batch_pages")
class HomepagePretrainedCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_template_detail_link_labels_preserve_descriptive_role_names(self):
        cases = (
            ("Project Manager", "View the Project Manager AI employee"),
            (
                "Candidate Sourcing AI Employee",
                "View the Candidate Sourcing AI Employee",
            ),
            (
                "candidate sourcing ai employee",
                "View the candidate sourcing ai employee",
            ),
            (
                "B2B Lead Research AI Agent",
                "View the B2B Lead Research AI Employee",
            ),
            ("  Project Manager  ", "View the Project Manager AI employee"),
        )
        for display_name, expected_label in cases:
            with self.subTest(display_name=display_name):
                template = SimpleNamespace(
                    code=f"template-{display_name.strip().lower().replace(' ', '-')}",
                    display_name=display_name,
                    tagline="Complete a useful workflow.",
                    description="Complete a useful workflow for a team.",
                    charter="Complete the workflow.",
                    base_schedule="@daily",
                    schedule_jitter_minutes=0,
                    event_triggers=[],
                    default_tools=[],
                    recommended_contact_channel="email",
                    category="Operations",
                    hero_image_path="",
                    priority=10,
                    is_active=True,
                    show_on_homepage=True,
                )

                serialized = _serialize_template(template, {})

                self.assertEqual(
                    serialized["detail_link_label"],
                    expected_label,
                )

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

    def test_unlisting_template_invalidates_all_public_template_caches(self):
        template = PersistentAgentTemplate.objects.create(
            code="cached-public-template",
            display_name="Cached public template",
            tagline="Cached",
            description="Cached public template",
            charter="Run the cached workflow.",
            is_listed=True,
        )
        cache_keys = [
            _homepage_pretrained_cache_key(),
            _homepage_pretrained_cache_lock_key(),
            LIBRARY_CACHE_KEY,
            LIBRARY_OFFICIAL_CACHE_KEY,
            LIBRARY_CATEGORY_SLUG_MAP_CACHE_KEY,
        ]
        cache.set_many({key: "stale-public-data" for key in cache_keys})

        with self.captureOnCommitCallbacks(execute=True):
            template.is_listed = False
            template.save(update_fields=["is_listed"])

        self.assertEqual(cache.get_many(cache_keys), {})


@tag("batch_pages")
class HomepageIntegrationsCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("pages.homepage_cache._build_homepage_integrations_payload")
    @patch("pages.homepage_cache._enqueue_homepage_integrations_refresh")
    def test_cache_miss_populates_cache(self, mock_enqueue, mock_build):
        mock_build.return_value = {"enabled": True, "builtins": [{"slug": "slack"}]}

        result = get_homepage_integrations_payload()

        self.assertEqual(result, mock_build.return_value)
        mock_enqueue.assert_not_called()

        cache_entry = cache.get(_homepage_integrations_cache_key())
        self.assertIsNotNone(cache_entry)
        self.assertEqual(cache_entry["data"], mock_build.return_value)

    @patch("pages.homepage_cache._build_homepage_integrations_payload")
    @patch("pages.homepage_cache._enqueue_homepage_integrations_refresh")
    def test_fresh_cache_hit_skips_refresh(self, mock_enqueue, mock_build):
        cached_data = {"enabled": True, "builtins": [{"slug": "slack"}]}
        cache.set(
            _homepage_integrations_cache_key(),
            {"data": cached_data, "refreshed_at": timezone.now().timestamp()},
            timeout=HOMEPAGE_INTEGRATIONS_CACHE_STALE_SECONDS,
        )

        result = get_homepage_integrations_payload()

        self.assertEqual(result, cached_data)
        mock_build.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch("pages.homepage_cache._build_homepage_integrations_payload")
    @patch("pages.homepage_cache._enqueue_homepage_integrations_refresh")
    def test_stale_cache_triggers_refresh(self, mock_enqueue, mock_build):
        self.assertGreater(
            HOMEPAGE_INTEGRATIONS_CACHE_STALE_SECONDS,
            HOMEPAGE_INTEGRATIONS_CACHE_FRESH_SECONDS,
        )
        cached_data = {"enabled": True, "builtins": [{"slug": "slack"}]}
        cache.set(
            _homepage_integrations_cache_key(),
            {
                "data": cached_data,
                "refreshed_at": timezone.now().timestamp()
                - (HOMEPAGE_INTEGRATIONS_CACHE_FRESH_SECONDS + 5),
            },
            timeout=HOMEPAGE_INTEGRATIONS_CACHE_STALE_SECONDS,
        )

        result = get_homepage_integrations_payload()

        self.assertEqual(result, cached_data)
        mock_build.assert_not_called()
        mock_enqueue.assert_called_once()

    @patch("pages.homepage_cache._platform_pipedream_server_is_active", return_value=False)
    def test_build_payload_returns_disabled_when_platform_server_is_inactive(self, _mock_is_active):
        result = _build_homepage_integrations_payload()

        self.assertEqual(result, {"enabled": True, "pipedream_enabled": False, "builtins": []})

    @override_settings(
        PIPEDREAM_CLIENT_ID="",
        PIPEDREAM_CLIENT_SECRET="",
        PIPEDREAM_PROJECT_ID="",
    )
    @patch("pages.homepage_cache._platform_pipedream_server_is_active", return_value=True)
    def test_build_payload_returns_disabled_when_pipedream_is_not_configured(self, _mock_is_active):
        result = _build_homepage_integrations_payload()

        self.assertEqual(result, {"enabled": True, "pipedream_enabled": False, "builtins": []})

    @override_settings(
        PIPEDREAM_CLIENT_ID="test-client-id",
        PIPEDREAM_CLIENT_SECRET="test-client-secret",
        PIPEDREAM_PROJECT_ID="test-project-id",
    )
    @patch("pages.homepage_cache.PipedreamCatalogService.get_apps")
    def test_build_payload_hides_deprecated_platform_apps(self, mock_get_apps):
        MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="pipedream",
            display_name="Pipedream",
            url="https://remote.mcp.pipedream.net",
            is_active=True,
            prefetch_apps=["google_sheets", "google_docs"],
            metadata={"deprecated_apps": ["google_sheets"]},
        )
        mock_get_apps.side_effect = lambda slugs: [
            type(
                "App",
                (),
                {
                    "slug": slug,
                    "to_dict": lambda self, slug=slug: {
                        "slug": slug,
                        "name": slug.replace("_", " ").title(),
                        "description": "",
                        "icon_url": "",
                    },
                },
            )()
            for slug in slugs
        ]

        result = _build_homepage_integrations_payload()

        self.assertTrue(result["enabled"])
        self.assertTrue(result["pipedream_enabled"])
        self.assertEqual([app["slug"] for app in result["builtins"]], ["google_docs"])
