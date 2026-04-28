import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
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
        self.assertIsNone(
            preferences.get(UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED),
        )
        self.assertTrue(
            preferences.get(UserPreference.KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED),
        )
        self.assertEqual(
            preferences.get(UserPreference.KEY_USER_TIMEZONE),
            "",
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

    def test_patch_updates_insights_panel_expanded_preference(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED: False,
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        preferences = response.json().get("preferences", {})
        self.assertFalse(preferences.get(UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED))

        stored = UserPreference.objects.get(user=self.user)
        self.assertFalse(
            (stored.preferences or {}).get(UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED)
        )

    def test_patch_allows_resetting_insights_panel_expanded_preference_to_auto(self):
        UserPreference.update_known_preferences(
            self.user,
            {
                UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED: True,
            },
        )

        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED: None,
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        preferences = response.json().get("preferences", {})
        self.assertIsNone(preferences.get(UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED))

    def test_patch_updates_agent_chat_notifications_enabled_preference(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED: False,
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        preferences = response.json().get("preferences", {})
        self.assertFalse(preferences.get(UserPreference.KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED))

        stored = UserPreference.objects.get(user=self.user)
        self.assertFalse(
            (stored.preferences or {}).get(UserPreference.KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED)
        )

    def test_patch_rejects_invalid_agent_chat_notifications_enabled_preference(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED: "yes",
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserPreference.objects.filter(user=self.user).exists())

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

    def test_patch_updates_timezone_preference(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_USER_TIMEZONE: "America/New_York",
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        preferences = response.json().get("preferences", {})
        self.assertEqual(
            preferences.get(UserPreference.KEY_USER_TIMEZONE),
            "America/New_York",
        )

    def test_patch_rejects_invalid_timezone_preference(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_USER_TIMEZONE: "Not/A_Real_Zone",
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_console_api_infers_timezone_when_preference_blank(self):
        response = self.client.get(
            self.url,
            HTTP_X_GOBII_TIMEZONE="America/Los_Angeles",
        )
        self.assertEqual(response.status_code, 200)
        preferences = response.json().get("preferences", {})
        self.assertEqual(
            preferences.get(UserPreference.KEY_USER_TIMEZONE),
            "America/Los_Angeles",
        )

    def test_console_api_inference_does_not_override_explicit_timezone(self):
        UserPreference.update_known_preferences(
            self.user,
            {UserPreference.KEY_USER_TIMEZONE: "Europe/Berlin"},
        )

        response = self.client.get(
            self.url,
            HTTP_X_GOBII_TIMEZONE="America/Los_Angeles",
        )
        self.assertEqual(response.status_code, 200)
        preferences = response.json().get("preferences", {})
        self.assertEqual(
            preferences.get(UserPreference.KEY_USER_TIMEZONE),
            "Europe/Berlin",
        )

    def test_console_api_inference_works_for_login_required_console_views(self):
        response = self.client.get(
            reverse("console_session"),
            HTTP_X_GOBII_TIMEZONE="America/Denver",
        )
        self.assertEqual(response.status_code, 200)

        preferences = UserPreference.resolve_known_preferences(self.user)
        self.assertEqual(
            preferences.get(UserPreference.KEY_USER_TIMEZONE),
            "America/Denver",
        )

    def test_console_api_ignores_invalid_timezone_header(self):
        response = self.client.get(
            reverse("console_session"),
            HTTP_X_GOBII_TIMEZONE="Not/A_Real_Zone",
        )
        self.assertEqual(response.status_code, 200)

        self.assertFalse(UserPreference.objects.filter(user=self.user).exists())

    def test_update_known_preferences_recovers_from_concurrent_create_race(self):
        existing = UserPreference.objects.create(user=self.user, preferences={})

        with patch.object(UserPreference.objects, "get_or_create", side_effect=IntegrityError("duplicate key")):
            resolved = UserPreference.update_known_preferences(
                self.user,
                {UserPreference.KEY_USER_TIMEZONE: "America/Los_Angeles"},
            )

        self.assertEqual(
            resolved.get(UserPreference.KEY_USER_TIMEZONE),
            "America/Los_Angeles",
        )
        existing.refresh_from_db()
        self.assertEqual(
            (existing.preferences or {}).get(UserPreference.KEY_USER_TIMEZONE),
            "America/Los_Angeles",
        )

    def test_update_known_preferences_retries_create_if_row_disappears_after_integrity_error(self):
        replacement = UserPreference.objects.create(user=self.user, preferences={})

        with patch.object(
            UserPreference.objects,
            "get_or_create",
            side_effect=[IntegrityError("duplicate key"), (replacement, False)],
        ), patch.object(UserPreference.objects, "get", side_effect=UserPreference.DoesNotExist):
            resolved = UserPreference.update_known_preferences(
                self.user,
                {UserPreference.KEY_USER_TIMEZONE: "America/Los_Angeles"},
            )

        self.assertEqual(
            resolved.get(UserPreference.KEY_USER_TIMEZONE),
            "America/Los_Angeles",
        )
        self.assertEqual(
            replacement.preferences.get(UserPreference.KEY_USER_TIMEZONE),
            "America/Los_Angeles",
        )

    def test_console_session_returns_user_identity_without_chat_mode_fields(self):
        response = self.client.get(reverse("console_session"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("user_id"), str(self.user.id))
        self.assertEqual(payload.get("email"), self.user.email)
        self.assertNotIn("simplified_chat_ui", payload)
        self.assertNotIn("simplified_chat_toggle_available", payload)

    def test_profile_page_updates_timezone_preference(self):
        response = self.client.post(
            reverse("profile"),
            {
                "first_name": "Timezone",
                "last_name": "Owner",
                "timezone": "America/Chicago",
            },
        )
        self.assertEqual(response.status_code, 302)

        preferences = UserPreference.resolve_known_preferences(self.user)
        self.assertEqual(
            preferences.get(UserPreference.KEY_USER_TIMEZONE),
            "America/Chicago",
        )
