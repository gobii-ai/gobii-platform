from django.test import TestCase, Client, tag
from django.urls import reverse
from django.contrib.auth import get_user_model

from api.models import LLMRoutingProfile, ProfileBrowserTier


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
        ProfileBrowserTier.objects.create(profile=profile, order=1, is_premium=False)

        url = reverse("console_llm_profile_browser_tiers", args=[profile.id])
        resp = self.client.post(url, data='{}', content_type="application/json")

        self.assertEqual(resp.status_code, 200, resp.content)
        tiers = list(ProfileBrowserTier.objects.filter(profile=profile, is_premium=False).order_by("order"))
        self.assertEqual(len(tiers), 2)
        self.assertEqual(tiers[-1].order, 2)

    def test_duplicate_order_request_is_bumped_to_next_available(self):
        profile = LLMRoutingProfile.objects.create(name="browser-dup", display_name="Browser Dup")
        ProfileBrowserTier.objects.create(profile=profile, order=1, is_premium=False)

        url = reverse("console_llm_profile_browser_tiers", args=[profile.id])
        resp = self.client.post(
            url,
            data='{"order": 1}',
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        tiers = list(ProfileBrowserTier.objects.filter(profile=profile, is_premium=False).order_by("order"))
        self.assertEqual(len(tiers), 2)
        self.assertEqual(tiers[-1].order, 2)

    def test_move_browser_tier_swaps_order(self):
        profile = LLMRoutingProfile.objects.create(name="browser-move", display_name="Browser Move")
        tier1 = ProfileBrowserTier.objects.create(profile=profile, order=1, is_premium=False, description="Tier 1")
        tier2 = ProfileBrowserTier.objects.create(profile=profile, order=2, is_premium=False, description="Tier 2")

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
