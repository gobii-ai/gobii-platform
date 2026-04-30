from django.test import TestCase, Client, tag
from django.urls import reverse
from django.contrib.auth import get_user_model

from api.models import (
    LLMProvider,
    LLMRoutingProfile,
    PersistentLLMTier,
    PersistentModelEndpoint,
    PersistentTierEndpoint,
    PersistentTokenRange,
    ProfilePersistentTier,
    ProfilePersistentTierEndpoint,
    ProfileTokenRange,
)
from console.llm_serializers import build_llm_overview
from tests.utils.llm_seed import get_intelligence_tier


@tag("batch_console_api")
class ConsoleProfilePersistentTierTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username="admin-persistent@example.com",
            email="admin-persistent@example.com",
            password="pass1234",
            is_staff=True,
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def test_move_profile_persistent_tier_swaps_order(self):
        profile = LLMRoutingProfile.objects.create(name="persist-move", display_name="Persist Move")
        token_range = ProfileTokenRange.objects.create(profile=profile, name="default", min_tokens=0)
        tier1 = ProfilePersistentTier.objects.create(
            token_range=token_range,
            order=1,
            intelligence_tier=get_intelligence_tier("standard"),
        )
        tier2 = ProfilePersistentTier.objects.create(
            token_range=token_range,
            order=2,
            intelligence_tier=get_intelligence_tier("standard"),
        )

        move_url = reverse("console_llm_profile_persistent_tier_detail", args=[tier2.id])
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

    def test_persistent_endpoint_delete_reports_tier_usage_and_force_detaches(self):
        provider = LLMProvider.objects.create(key="provider", display_name="Provider", enabled=True)
        endpoint = PersistentModelEndpoint.objects.create(
            provider=provider,
            key="persistent-model",
            litellm_model="provider/persistent-model",
            enabled=True,
        )
        standard_tier = get_intelligence_tier("standard")
        token_range = PersistentTokenRange.objects.create(name="large-delete", min_tokens=20000)
        legacy_tier = PersistentLLMTier.objects.create(
            token_range=token_range,
            order=4,
            intelligence_tier=standard_tier,
            description="Large context route",
        )
        legacy_ref = PersistentTierEndpoint.objects.create(
            tier=legacy_tier,
            endpoint=endpoint,
            weight=1.0,
        )
        profile = LLMRoutingProfile.objects.create(
            name="profile-persistent-default",
            display_name="Profile Persistent Default",
            is_active=False,
            summarization_endpoint=endpoint,
        )
        profile_range = ProfileTokenRange.objects.create(
            profile=profile,
            name="large",
            min_tokens=20000,
        )
        profile_tier = ProfilePersistentTier.objects.create(
            token_range=profile_range,
            order=4,
            intelligence_tier=standard_tier,
            description="Profile large context route",
        )
        profile_ref = ProfilePersistentTierEndpoint.objects.create(
            tier=profile_tier,
            endpoint=endpoint,
            weight=1.0,
        )

        url = reverse("console_llm_persistent_endpoint_detail", args=[endpoint.id])
        blocked_resp = self.client.delete(url)

        self.assertEqual(blocked_resp.status_code, 409, blocked_resp.content)
        payload = blocked_resp.json()
        self.assertEqual(payload["code"], "endpoint_in_tiers")
        self.assertEqual(
            {(entry["routing_profile"], entry["tier"], entry["role"]) for entry in payload["tier_usage"]},
            {
                ("Default persistent config", "large-delete / Standard tier 4", "primary"),
                ("Profile Persistent Default", "large / Standard tier 4", "primary"),
                ("Profile Persistent Default", "Summarization override", "summarization"),
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
                ("Default persistent config", "large-delete / Standard tier 4", "primary"),
                ("Profile Persistent Default", "large / Standard tier 4", "primary"),
                ("Profile Persistent Default", "Summarization override", "summarization"),
            },
        )

        force_resp = self.client.delete(f"{url}?force=1")

        self.assertEqual(force_resp.status_code, 200, force_resp.content)
        self.assertFalse(PersistentModelEndpoint.objects.filter(id=endpoint.id).exists())
        self.assertFalse(PersistentTierEndpoint.objects.filter(id=legacy_ref.id).exists())
        self.assertFalse(ProfilePersistentTierEndpoint.objects.filter(id=profile_ref.id).exists())
        profile.refresh_from_db()
        self.assertIsNone(profile.summarization_endpoint_id)
