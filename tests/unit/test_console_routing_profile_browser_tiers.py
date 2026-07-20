from django.test import TestCase, Client, tag
from django.urls import reverse
from django.contrib.auth import get_user_model

from api.models import (
    BrowserLLMPolicy,
    BrowserLLMTier,
    BrowserModelEndpoint,
    BrowserTierEndpoint,
    LLMProvider,
    LLMRoutingProfile,
    ProfileBrowserTier,
    ProfileBrowserTierEndpoint,
)
from console.llm_serializers import build_llm_overview
from tests.utils.llm_seed import get_intelligence_tier


@tag("batch_console_api")
class ConsoleRoutingProfileBrowserTierTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username="admin@example.com",
            email="admin@example.com",
            password="pass1234",
            is_staff=True,
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def test_creating_browser_tier_without_order_appends_next(self):
        profile = LLMRoutingProfile.objects.create(name="browser-default", display_name="Browser Default")
        standard_tier = get_intelligence_tier("standard")
        ProfileBrowserTier.objects.create(profile=profile, order=1, intelligence_tier=standard_tier)

        url = reverse("console_llm_profile_browser_tiers", args=[profile.id])
        resp = self.client.post(url, data='{}', content_type="application/json")

        self.assertEqual(resp.status_code, 200, resp.content)
        tiers = list(ProfileBrowserTier.objects.filter(profile=profile, intelligence_tier=standard_tier).order_by("order"))
        self.assertEqual(len(tiers), 2)
        self.assertEqual(tiers[-1].order, 2)

    def test_duplicate_order_request_is_bumped_to_next_available(self):
        profile = LLMRoutingProfile.objects.create(name="browser-dup", display_name="Browser Dup")
        standard_tier = get_intelligence_tier("standard")
        ProfileBrowserTier.objects.create(profile=profile, order=1, intelligence_tier=standard_tier)

        url = reverse("console_llm_profile_browser_tiers", args=[profile.id])
        resp = self.client.post(
            url,
            data='{"order": 1}',
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        tiers = list(ProfileBrowserTier.objects.filter(profile=profile, intelligence_tier=standard_tier).order_by("order"))
        self.assertEqual(len(tiers), 2)
        self.assertEqual(tiers[-1].order, 2)

    def test_move_browser_tier_swaps_order(self):
        profile = LLMRoutingProfile.objects.create(name="browser-move", display_name="Browser Move")
        standard_tier = get_intelligence_tier("standard")
        tier1 = ProfileBrowserTier.objects.create(
            profile=profile,
            order=1,
            intelligence_tier=standard_tier,
            description="Tier 1",
        )
        tier2 = ProfileBrowserTier.objects.create(
            profile=profile,
            order=2,
            intelligence_tier=standard_tier,
            description="Tier 2",
        )

        move_url = reverse("console_llm_profile_browser_tier_detail", args=[tier2.id])
        resp = self.client.patch(move_url, data='{"move": "up"}', content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)

        tier1.refresh_from_db()
        tier2.refresh_from_db()
        self.assertEqual(tier1.order, 2)
        self.assertEqual(tier2.order, 1)

        resp = self.client.patch(move_url, data='{"move": "down"}', content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)

        tier1.refresh_from_db()
        tier2.refresh_from_db()
        self.assertEqual(tier1.order, 1)
        self.assertEqual(tier2.order, 2)

    def test_system_and_profile_browser_endpoint_mutations_have_parity(self):
        provider = LLMProvider.objects.create(key="browser-parity", display_name="Browser Parity", enabled=True)
        endpoint = BrowserModelEndpoint.objects.create(
            provider=provider,
            key="browser-primary",
            browser_model="browser-primary",
            enabled=True,
        )
        extraction = BrowserModelEndpoint.objects.create(
            provider=provider,
            key="browser-extraction",
            browser_model="browser-extraction",
            enabled=True,
        )
        intelligence_tier = get_intelligence_tier("standard")
        policy = BrowserLLMPolicy.objects.create(name="Parity", is_active=True)
        system_tier = BrowserLLMTier.objects.create(
            policy=policy,
            order=1,
            intelligence_tier=intelligence_tier,
        )
        profile = LLMRoutingProfile.objects.create(name="browser-parity", display_name="Browser Parity")
        profile_tier = ProfileBrowserTier.objects.create(
            profile=profile,
            order=1,
            intelligence_tier=intelligence_tier,
        )

        cases = (
            (
                reverse("console_llm_browser_tier_endpoints", args=[system_tier.id]),
                "console_llm_browser_tier_endpoint_detail",
                BrowserTierEndpoint,
            ),
            (
                reverse("console_llm_profile_browser_tier_endpoints", args=[profile_tier.id]),
                "console_llm_profile_browser_tier_endpoint_detail",
                ProfileBrowserTierEndpoint,
            ),
        )
        for create_url, detail_name, model in cases:
            with self.subTest(create_url=create_url):
                created = self.client.post(
                    create_url,
                    data=(
                        f'{{"endpoint_id": "{endpoint.id}", "extraction_endpoint_id": '
                        f'"{extraction.id}", "weight": 1}}'
                    ),
                    content_type="application/json",
                )
                self.assertEqual(created.status_code, 200, created.content)
                tier_endpoint = model.objects.get(id=created.json()["tier_endpoint_id"])
                self.assertEqual(tier_endpoint.extraction_endpoint_id, extraction.id)

                detail_url = reverse(detail_name, args=[tier_endpoint.id])
                invalid = self.client.patch(
                    detail_url,
                    data='{"weight": -1}',
                    content_type="application/json",
                )
                self.assertEqual(invalid.status_code, 400, invalid.content)
                updated = self.client.patch(
                    detail_url,
                    data='{"weight": 2, "extraction_endpoint_id": null}',
                    content_type="application/json",
                )
                self.assertEqual(updated.status_code, 200, updated.content)
                tier_endpoint.refresh_from_db()
                self.assertEqual(float(tier_endpoint.weight), 2)
                self.assertIsNone(tier_endpoint.extraction_endpoint_id)
                self.assertEqual(self.client.delete(detail_url).status_code, 200)
                self.assertFalse(model.objects.filter(id=tier_endpoint.id).exists())

    def test_browser_endpoint_delete_reports_tier_usage_and_force_detaches(self):
        provider = LLMProvider.objects.create(key="provider", display_name="Provider", enabled=True)
        endpoint = BrowserModelEndpoint.objects.create(
            provider=provider,
            key="browser-model",
            browser_model="model/browser",
            enabled=True,
        )
        standard_tier = get_intelligence_tier("standard")
        policy = BrowserLLMPolicy.objects.create(name="Default", is_active=True)
        legacy_tier = BrowserLLMTier.objects.create(
            policy=policy,
            order=1,
            intelligence_tier=standard_tier,
            description="Primary browser route",
        )
        legacy_ref = BrowserTierEndpoint.objects.create(
            tier=legacy_tier,
            endpoint=endpoint,
            weight=0.75,
        )
        profile = LLMRoutingProfile.objects.create(
            name="profile-default",
            display_name="Profile Default",
            is_active=False,
        )
        profile_tier = ProfileBrowserTier.objects.create(
            profile=profile,
            order=2,
            intelligence_tier=standard_tier,
            description="Profile browser route",
        )
        profile_ref = ProfileBrowserTierEndpoint.objects.create(
            tier=profile_tier,
            endpoint=endpoint,
            weight=0.5,
        )

        url = reverse("console_llm_browser_endpoint_detail", args=[endpoint.id])
        blocked_resp = self.client.delete(url)

        self.assertEqual(blocked_resp.status_code, 409, blocked_resp.content)
        payload = blocked_resp.json()
        self.assertEqual(payload["code"], "endpoint_in_tiers")
        self.assertEqual(len(payload["tier_usage"]), 2)
        self.assertEqual(
            {(entry["routing_profile"], entry["tier"], entry["role"]) for entry in payload["tier_usage"]},
            {
                ("Default", "Standard tier 1", "primary"),
                ("Profile Default", "Standard tier 2", "primary"),
            },
        )
        overview = build_llm_overview()
        endpoint_payload = next(
            entry
            for provider_entry in overview["providers"]
            for entry in provider_entry["endpoints"]
            if entry["id"] == str(endpoint.id)
        )
        self.assertEqual(
            {(entry["routing_profile"], entry["tier"], entry["role"]) for entry in endpoint_payload["tier_usage"]},
            {
                ("Default", "Standard tier 1", "primary"),
                ("Profile Default", "Standard tier 2", "primary"),
            },
        )

        force_resp = self.client.delete(f"{url}?force=1")

        self.assertEqual(force_resp.status_code, 200, force_resp.content)
        self.assertFalse(BrowserModelEndpoint.objects.filter(id=endpoint.id).exists())
        self.assertFalse(BrowserTierEndpoint.objects.filter(id=legacy_ref.id).exists())
        self.assertFalse(ProfileBrowserTierEndpoint.objects.filter(id=profile_ref.id).exists())
