import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from api.models import UserPreference


@tag("batch_console_api")
class ConsoleUserPreferencesApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="preferences-owner",
            email="preferences-owner@example.com",
            password="password123",
        )
        self.client.force_login(self.user)
        self.url = reverse("console_user_preferences")

    def test_get_defaults_to_recent_when_preference_row_missing(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        preferences = payload.get("preferences", {})
        self.assertEqual(
            preferences.get(UserPreference.KEY_AGENT_CHAT_ROSTER_SORT_MODE),
            UserPreference.AgentRosterSortMode.RECENT,
        )
        self.assertEqual(
            preferences.get(UserPreference.KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS),
            [],
        )

    def test_patch_updates_preference_and_get_returns_persisted_value(self):
        patch_response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_ROSTER_SORT_MODE: "alphabetical",
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(patch_response.status_code, 200)
        patch_payload = patch_response.json()
        patch_preferences = patch_payload.get("preferences", {})
        self.assertEqual(
            patch_preferences.get(UserPreference.KEY_AGENT_CHAT_ROSTER_SORT_MODE),
            UserPreference.AgentRosterSortMode.ALPHABETICAL,
        )

        stored = UserPreference.objects.get(user=self.user)
        self.assertEqual(
            (stored.preferences or {}).get(UserPreference.KEY_AGENT_CHAT_ROSTER_SORT_MODE),
            UserPreference.AgentRosterSortMode.ALPHABETICAL,
        )

        get_response = self.client.get(self.url)
        self.assertEqual(get_response.status_code, 200)
        get_payload = get_response.json()
        get_preferences = get_payload.get("preferences", {})
        self.assertEqual(
            get_preferences.get(UserPreference.KEY_AGENT_CHAT_ROSTER_SORT_MODE),
            UserPreference.AgentRosterSortMode.ALPHABETICAL,
        )

    def test_patch_rejects_invalid_sort_mode(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_ROSTER_SORT_MODE: "newest",
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserPreference.objects.filter(user=self.user).exists())

    def test_patch_rejects_unknown_key(self):
        response = self.client.patch(
            self.url,
            data=json.dumps({"preferences": {"unknown.key": "anything"}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserPreference.objects.filter(user=self.user).exists())

    def test_patch_updates_favorite_agent_ids_and_dedupes(self):
        favorite_agent_id = str(uuid.uuid4())
        duplicate_agent_id = favorite_agent_id.upper()
        second_agent_id = str(uuid.uuid4())

        patch_response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS: [
                            favorite_agent_id,
                            duplicate_agent_id,
                            second_agent_id,
                        ],
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(patch_response.status_code, 200)
        patch_preferences = patch_response.json().get("preferences", {})
        self.assertEqual(
            patch_preferences.get(UserPreference.KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS),
            [favorite_agent_id, second_agent_id],
        )

        stored = UserPreference.objects.get(user=self.user)
        self.assertEqual(
            (stored.preferences or {}).get(UserPreference.KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS),
            [favorite_agent_id, second_agent_id],
        )

    def test_patch_rejects_invalid_favorite_agent_ids(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS: ["not-a-uuid"],
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserPreference.objects.filter(user=self.user).exists())

    def test_patch_rejects_unknown_top_level_fields(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_ROSTER_SORT_MODE: UserPreference.AgentRosterSortMode.RECENT,
                    },
                    "extra": True,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserPreference.objects.filter(user=self.user).exists())
