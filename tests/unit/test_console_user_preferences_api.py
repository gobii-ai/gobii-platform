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
        self.assertEqual(
            preferences.get(UserPreference.KEY_AGENT_CHAT_SIMPLIFIED_ENABLED),
            False,
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

    def test_patch_updates_simplified_chat_preference(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_SIMPLIFIED_ENABLED: True,
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        preferences = response.json().get("preferences", {})
        self.assertEqual(
            preferences.get(UserPreference.KEY_AGENT_CHAT_SIMPLIFIED_ENABLED),
            True,
        )

    def test_optional_simplified_chat_preference_resolver_distinguishes_unset(self):
        self.assertIsNone(UserPreference.resolve_optional_simplified_chat_enabled(self.user))

        UserPreference.update_known_preferences(
            self.user,
            {UserPreference.KEY_AGENT_CHAT_SIMPLIFIED_ENABLED: False},
        )
        self.assertFalse(UserPreference.resolve_optional_simplified_chat_enabled(self.user))

        UserPreference.update_known_preferences(
            self.user,
            {UserPreference.KEY_AGENT_CHAT_SIMPLIFIED_ENABLED: True},
        )
        self.assertTrue(UserPreference.resolve_optional_simplified_chat_enabled(self.user))

    def test_patch_rejects_invalid_simplified_chat_preference(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_SIMPLIFIED_ENABLED: "true",
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

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

    def test_console_session_hides_toggle_and_forces_normal_when_flag_disabled(self):
        from waffle.testutils import override_flag

        UserPreference.update_known_preferences(
            self.user,
            {UserPreference.KEY_AGENT_CHAT_SIMPLIFIED_ENABLED: True},
        )

        with override_flag("simplified_chat_ui", active=False), override_flag("simplified_chat_default_conversational", active=True):
            response = self.client.get(reverse("console_session"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload.get("simplified_chat_ui"))
        self.assertFalse(payload.get("simplified_chat_toggle_available"))

    def test_console_session_exposes_toggle_and_effective_state_when_flag_enabled(self):
        from waffle.testutils import override_flag

        with override_flag("simplified_chat_ui", active=True), override_flag("simplified_chat_default_conversational", active=False):
            response = self.client.get(reverse("console_session"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("simplified_chat_ui"))
        self.assertFalse(payload.get("simplified_chat_toggle_available"))

        with override_flag("simplified_chat_ui", active=True), override_flag("simplified_chat_default_conversational", active=True):
            default_response = self.client.get(reverse("console_session"))
        self.assertEqual(default_response.status_code, 200)
        default_payload = default_response.json()
        self.assertTrue(default_payload.get("simplified_chat_ui"))
        self.assertFalse(default_payload.get("simplified_chat_toggle_available"))

        UserPreference.update_known_preferences(
            self.user,
            {UserPreference.KEY_AGENT_CHAT_SIMPLIFIED_ENABLED: True},
        )

        with override_flag("simplified_chat_ui", active=True), override_flag("simplified_chat_default_conversational", active=False):
            enabled_response = self.client.get(reverse("console_session"))
        self.assertEqual(enabled_response.status_code, 200)
        enabled_payload = enabled_response.json()
        self.assertTrue(enabled_payload.get("simplified_chat_ui"))
        self.assertFalse(enabled_payload.get("simplified_chat_toggle_available"))

        UserPreference.update_known_preferences(
            self.user,
            {UserPreference.KEY_AGENT_CHAT_SIMPLIFIED_ENABLED: False},
        )

        with override_flag("simplified_chat_ui", active=True), override_flag("simplified_chat_default_conversational", active=True):
            saved_false_response = self.client.get(reverse("console_session"))
        self.assertEqual(saved_false_response.status_code, 200)
        saved_false_payload = saved_false_response.json()
        self.assertTrue(saved_false_payload.get("simplified_chat_ui"))
        self.assertFalse(saved_false_payload.get("simplified_chat_toggle_available"))

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
