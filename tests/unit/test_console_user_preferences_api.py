import json
import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
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
            preferences.get(UserPreference.KEY_AGENT_CHAT_MUTED_AGENT_IDS),
            [],
        )
        self.assertIsNone(
            preferences.get(UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED),
        )
        self.assertEqual(
            preferences.get(UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT),
            {},
        )
        self.assertTrue(
            preferences.get(UserPreference.KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED),
        )
        self.assertTrue(
            preferences.get(UserPreference.KEY_AGENT_CHAT_SUGGESTIONS_ENABLED),
        )
        self.assertEqual(
            preferences.get(UserPreference.KEY_USER_TIMEZONE),
            "",
        )
        self.assertTrue(preferences.get(UserPreference.KEY_USER_PET_ENABLED))
        self.assertEqual(
            preferences.get(UserPreference.KEY_USER_PET_SELECTED_ID),
            "builtin:gobii-fish",
        )
        self.assertEqual(
            preferences.get(UserPreference.KEY_USER_PET_SIZE),
            "medium",
        )
        self.assertIsNone(preferences.get(UserPreference.KEY_USER_PET_POSITION))

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

    def test_patch_rejects_pet_position_outside_viewport(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_USER_PET_POSITION: {"x": 1.1, "y": 0.5},
                    }
                }
            ),
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

    def test_patch_updates_muted_agent_ids_and_dedupes(self):
        muted_agent_id = str(uuid.uuid4())
        duplicate_agent_id = muted_agent_id.upper()
        second_agent_id = str(uuid.uuid4())

        patch_response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_MUTED_AGENT_IDS: [
                            muted_agent_id,
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
            patch_preferences.get(UserPreference.KEY_AGENT_CHAT_MUTED_AGENT_IDS),
            [muted_agent_id, second_agent_id],
        )

        stored = UserPreference.objects.get(user=self.user)
        self.assertEqual(
            (stored.preferences or {}).get(UserPreference.KEY_AGENT_CHAT_MUTED_AGENT_IDS),
            [muted_agent_id, second_agent_id],
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

    def test_patch_merges_insights_panel_preferences_by_agent(self):
        first_agent_id = uuid.uuid4()
        second_agent_id = uuid.uuid4()

        first_response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT: {
                            str(first_agent_id).upper(): False,
                        },
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(first_response.status_code, 200)

        second_response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT: {
                            str(second_agent_id): True,
                        },
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(second_response.status_code, 200)
        expected = {
            str(first_agent_id): False,
            str(second_agent_id): True,
        }
        self.assertEqual(
            second_response.json()["preferences"][
                UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT
            ],
            expected,
        )

        stored = UserPreference.objects.get(user=self.user)
        self.assertEqual(
            stored.preferences[UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT],
            expected,
        )

    def test_patch_rejects_invalid_insights_panel_preferences_by_agent(self):
        valid_agent_id = str(uuid.uuid4())
        invalid_values = (
            [],
            {"not-an-agent-uuid": False},
            {valid_agent_id: "false"},
            {valid_agent_id: False, valid_agent_id.upper(): True},
        )

        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                response = self.client.patch(
                    self.url,
                    data=json.dumps(
                        {
                            "preferences": {
                                UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT: invalid_value,
                            }
                        }
                    ),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertFalse(UserPreference.objects.filter(user=self.user).exists())

    def test_agent_scoped_preference_merge_preserves_unknown_stored_preferences(self):
        first_agent_id = str(uuid.uuid4())
        second_agent_id = str(uuid.uuid4())
        future_key = "agent.chat.future.preference"
        preference = UserPreference.objects.create(
            user=self.user,
            preferences={
                future_key: {"enabled": True},
                UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT: {
                    first_agent_id: False,
                },
            },
        )

        UserPreference.update_known_preferences(
            self.user,
            {
                UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT: {
                    second_agent_id: True,
                },
            },
        )

        preference.refresh_from_db()
        self.assertEqual(preference.preferences[future_key], {"enabled": True})
        self.assertEqual(
            preference.preferences[UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT],
            {
                first_agent_id: False,
                second_agent_id: True,
            },
        )

    def test_insights_panel_preferences_by_agent_are_isolated_per_user(self):
        agent_id = str(uuid.uuid4())
        other_user = get_user_model().objects.create_user(
            username="other-preferences-owner",
            email="other-preferences-owner@example.com",
            password="password123",
        )
        UserPreference.update_known_preferences(
            self.user,
            {
                UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT: {
                    agent_id: False,
                },
            },
        )
        UserPreference.update_known_preferences(
            other_user,
            {
                UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT: {
                    agent_id: True,
                },
            },
        )

        self.assertFalse(
            UserPreference.resolve_known_preferences(self.user)[
                UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT
            ][agent_id]
        )
        self.assertTrue(
            UserPreference.resolve_known_preferences(other_user)[
                UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED_BY_AGENT
            ][agent_id]
        )

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

    def test_patch_updates_agent_chat_suggestions_enabled_preference(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_SUGGESTIONS_ENABLED: False,
                    }
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        preferences = response.json().get("preferences", {})
        self.assertFalse(preferences.get(UserPreference.KEY_AGENT_CHAT_SUGGESTIONS_ENABLED))

        stored = UserPreference.objects.get(user=self.user)
        self.assertFalse(
            (stored.preferences or {}).get(UserPreference.KEY_AGENT_CHAT_SUGGESTIONS_ENABLED)
        )

    def test_patch_rejects_invalid_agent_chat_suggestions_enabled_preference(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_SUGGESTIONS_ENABLED: "no",
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

    def test_patch_rejects_invalid_muted_agent_ids(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "preferences": {
                        UserPreference.KEY_AGENT_CHAT_MUTED_AGENT_IDS: ["not-a-uuid"],
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

    def test_update_known_preferences_locks_and_merges_fresh_stored_preferences(self):
        existing = UserPreference.objects.create(
            user=self.user,
            preferences={UserPreference.KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED: False},
        )
        stale_preference = UserPreference(
            pk=existing.pk,
            user=self.user,
            preferences={},
        )
        select_for_update = UserPreference.objects.select_for_update

        def select_for_update_in_transaction(*args, **kwargs):
            self.assertTrue(connection.in_atomic_block)
            return select_for_update(*args, **kwargs)

        with patch.object(
            UserPreference.objects,
            "get_or_create",
            return_value=(stale_preference, False),
        ), patch.object(
            UserPreference.objects,
            "select_for_update",
            side_effect=select_for_update_in_transaction,
        ) as locked_query, patch.object(
            transaction,
            "atomic",
            wraps=transaction.atomic,
        ) as atomic:
            resolved = UserPreference.update_known_preferences(
                self.user,
                {UserPreference.KEY_USER_TIMEZONE: "America/Los_Angeles"},
            )

        atomic.assert_called_once_with()
        locked_query.assert_called_once_with()
        self.assertFalse(resolved[UserPreference.KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED])
        self.assertEqual(resolved[UserPreference.KEY_USER_TIMEZONE], "America/Los_Angeles")
        existing.refresh_from_db()
        self.assertEqual(
            existing.preferences,
            {
                UserPreference.KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED: False,
                UserPreference.KEY_USER_TIMEZONE: "America/Los_Angeles",
            },
        )

    def test_update_known_preferences_retries_create_if_row_disappears_after_integrity_error(self):
        replacement = UserPreference.objects.create(user=self.user, preferences={})
        missing_locked_query = MagicMock()
        missing_locked_query.get.side_effect = UserPreference.DoesNotExist
        locked_replacement_query = UserPreference.objects.select_for_update()

        with patch.object(
            UserPreference.objects,
            "get_or_create",
            side_effect=[IntegrityError("duplicate key"), (replacement, False)],
        ), patch.object(
            UserPreference.objects,
            "select_for_update",
            side_effect=[missing_locked_query, locked_replacement_query],
        ):
            resolved = UserPreference.update_known_preferences(
                self.user,
                {UserPreference.KEY_USER_TIMEZONE: "America/Los_Angeles"},
            )

        self.assertEqual(
            resolved.get(UserPreference.KEY_USER_TIMEZONE),
            "America/Los_Angeles",
        )
        replacement.refresh_from_db()
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
