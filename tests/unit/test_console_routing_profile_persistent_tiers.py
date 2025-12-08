from django.test import TestCase, Client, tag
from django.urls import reverse
from django.contrib.auth import get_user_model

from api.models import LLMRoutingProfile, ProfileTokenRange, ProfilePersistentTier


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
        tier1 = ProfilePersistentTier.objects.create(token_range=token_range, order=1, is_premium=False, is_max=False)
        tier2 = ProfilePersistentTier.objects.create(token_range=token_range, order=2, is_premium=False, is_max=False)

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
