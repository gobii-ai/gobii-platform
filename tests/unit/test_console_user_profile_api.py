import json
from unittest import skipUnless
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from api.models import AgentOwnerCustomInstructions, Organization, UserPhoneNumber, UserPreference
from api.services.agent_owner_custom_instructions import (
    get_custom_instructions_for_organization_id,
    get_custom_instructions_for_user_id,
    save_custom_instructions_for_organization_id,
    save_custom_instructions_for_user_id,
)


@tag("batch_console_api")
class ConsoleUserProfileApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="profile-owner",
            email="profile-owner@example.com",
            password="password123",
            first_name="Profile",
            last_name="Owner",
        )
        self.client.force_login(self.user)
        self.url = reverse("console_user_profile")

    def test_requires_authentication(self):
        response = self.client_class().get(self.url)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Authentication required"})

    def test_get_returns_profile_payload(self):
        UserPreference.update_known_preferences(
            self.user,
            {UserPreference.KEY_USER_TIMEZONE: "America/New_York"},
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        verified_at = timezone.now()
        UserPhoneNumber.objects.create(
            user=self.user,
            phone_number="+15551234567",
            is_primary=True,
            is_verified=True,
            verified_at=verified_at,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["profile"],
            {
                "firstName": "Profile",
                "lastName": "Owner",
                "timezone": "America/New_York",
            },
        )
        self.assertIn({"value": "", "label": "Auto-detect (from browser)"}, payload["timezoneOptions"])
        self.assertIn("/?ref=", payload["referralLink"])
        self.assertEqual(
            payload["emailVerification"],
            {"email": "profile-owner@example.com", "isVerified": True},
        )
        self.assertEqual(payload["phone"]["number"], "+15551234567")
        self.assertTrue(payload["phone"]["isVerified"])
        self.assertIsNotNone(payload["phone"]["verifiedAt"])

    @override_settings(AGENT_OWNER_CUSTOM_INSTRUCTIONS_MAX_CHARS=123)
    def test_get_returns_custom_instructions_settings(self):
        AgentOwnerCustomInstructions.objects.create(
            user=self.user,
            instructions="Use my concise operating style.",
            updated_by=self.user,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["customInstructions"], "Use my concise operating style.")
        self.assertEqual(payload["customInstructionsMaxChars"], 123)

    def test_patch_updates_profile_and_timezone(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "profile": {
                        "firstName": "Updated",
                        "lastName": "User",
                        "timezone": "Europe/London",
                    }
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Updated")
        self.assertEqual(self.user.last_name, "User")
        preferences = UserPreference.objects.get(user=self.user)
        self.assertEqual(
            preferences.preferences[UserPreference.KEY_USER_TIMEZONE],
            "Europe/London",
        )
        self.assertEqual(response.json()["profile"]["timezone"], "Europe/London")

    def test_patch_rejects_empty_payload(self):
        response = self.client.patch(
            self.url,
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("nonFieldErrors", response.json()["errors"])

    def test_patch_rejects_top_level_profile_fields(self):
        response = self.client.patch(
            self.url,
            data=json.dumps({"firstName": "Updated"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("nonFieldErrors", response.json()["errors"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Profile")

    def test_patch_updates_custom_instructions(self):
        response = self.client.patch(
            self.url,
            data=json.dumps({"customInstructions": "  Prefer short weekly summaries.\r\nInclude blockers.  "}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        instructions = AgentOwnerCustomInstructions.objects.get(user=self.user)
        self.assertEqual(instructions.instructions, "Prefer short weekly summaries.\nInclude blockers.")
        self.assertEqual(instructions.updated_by, self.user)
        self.assertEqual(response.json()["customInstructions"], "Prefer short weekly summaries.\nInclude blockers.")

    @override_settings(AGENT_OWNER_CUSTOM_INSTRUCTIONS_MAX_CHARS=5)
    def test_patch_rejects_over_limit_custom_instructions(self):
        response = self.client.patch(
            self.url,
            data=json.dumps({"customInstructions": "123456"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("customInstructions", response.json()["errors"])
        self.assertFalse(AgentOwnerCustomInstructions.objects.filter(user=self.user).exists())

    def test_patch_empty_custom_instructions_clears_existing_row(self):
        AgentOwnerCustomInstructions.objects.create(
            user=self.user,
            instructions="Existing personal instructions",
            updated_by=self.user,
        )

        response = self.client.patch(
            self.url,
            data=json.dumps({"customInstructions": " \r\n "}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(AgentOwnerCustomInstructions.objects.filter(user=self.user).exists())
        self.assertEqual(response.json()["customInstructions"], "")

    def test_patch_rejects_invalid_timezone(self):
        response = self.client.patch(
            self.url,
            data=json.dumps(
                {
                    "profile": {
                        "firstName": "Profile",
                        "lastName": "Owner",
                        "timezone": "Not/A_Timezone",
                    }
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("timezone", response.json()["errors"])


@tag("batch_console_api")
class AgentOwnerCustomInstructionsServiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="instructions-owner",
            email="instructions-owner@example.com",
            password="password123",
        )
        self.other_user = user_model.objects.create_user(
            username="instructions-other",
            email="instructions-other@example.com",
            password="password123",
        )
        self.org = Organization.objects.create(
            name="Instructions Org",
            slug="instructions-org",
            created_by=self.user,
        )

    def _create_legacy_both_owner_custom_instructions(self, *, user, organization, instructions: str):
        now = timezone.now()
        with connection.cursor() as cursor:
            try:
                cursor.execute("PRAGMA ignore_check_constraints = ON")
                cursor.execute(
                    """
                    INSERT INTO api_agentownercustominstructions
                        (user_id, organization_id, instructions, updated_by_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    [user.id, organization.id.hex, instructions, user.id, now, now],
                )
            finally:
                cursor.execute("PRAGMA ignore_check_constraints = OFF")

    def test_save_requires_valid_owner_id_before_delete(self):
        personal_instructions = AgentOwnerCustomInstructions.objects.create(
            user=self.other_user,
            instructions="Keep my personal instructions.",
            updated_by=self.other_user,
        )
        org_instructions = AgentOwnerCustomInstructions.objects.create(
            organization=self.org,
            instructions="Keep the org instructions.",
            updated_by=self.user,
        )

        with self.assertRaises(ValueError):
            save_custom_instructions_for_organization_id(None, instructions="", updated_by=self.user)
        with self.assertRaises(ValueError):
            save_custom_instructions_for_user_id(None, instructions="", updated_by=self.user)

        self.assertTrue(AgentOwnerCustomInstructions.objects.filter(pk=personal_instructions.pk).exists())
        self.assertTrue(AgentOwnerCustomInstructions.objects.filter(pk=org_instructions.pk).exists())

    @skipUnless(connection.vendor == "sqlite", "legacy both-owner regression uses sqlite constraint override")
    def test_owner_access_ignores_legacy_rows_with_both_owners(self):
        self._create_legacy_both_owner_custom_instructions(
            user=self.user,
            organization=self.org,
            instructions="Legacy shared instructions",
        )

        self.assertEqual(get_custom_instructions_for_organization_id(self.org.id), "")
        self.assertEqual(get_custom_instructions_for_user_id(self.user.id), "")

        save_custom_instructions_for_user_id(self.user.id, instructions="", updated_by=self.user)

        self.assertFalse(
            AgentOwnerCustomInstructions.objects.filter(user=self.user, organization=self.org).exists()
        )

    @skipUnless(connection.vendor == "sqlite", "legacy both-owner regression uses sqlite constraint override")
    def test_save_repairs_legacy_rows_with_both_owners_before_insert(self):
        self._create_legacy_both_owner_custom_instructions(
            user=self.user,
            organization=self.org,
            instructions="Legacy personal conflict",
        )

        save_custom_instructions_for_user_id(
            self.user.id,
            instructions="New personal instructions",
            updated_by=self.user,
        )

        personal_instructions = AgentOwnerCustomInstructions.objects.get(
            user=self.user,
            organization__isnull=True,
        )
        self.assertEqual(personal_instructions.instructions, "New personal instructions")
        self.assertFalse(
            AgentOwnerCustomInstructions.objects.filter(user=self.user, organization=self.org).exists()
        )

        other_org = Organization.objects.create(
            name="Other Instructions Org",
            slug="other-instructions-org",
            created_by=self.user,
        )
        self._create_legacy_both_owner_custom_instructions(
            user=self.other_user,
            organization=other_org,
            instructions="Legacy organization conflict",
        )

        save_custom_instructions_for_organization_id(
            other_org.id,
            instructions="New organization instructions",
            updated_by=self.user,
        )

        organization_instructions = AgentOwnerCustomInstructions.objects.get(
            user__isnull=True,
            organization=other_org,
        )
        self.assertEqual(organization_instructions.instructions, "New organization instructions")
        self.assertFalse(
            AgentOwnerCustomInstructions.objects.filter(user=self.other_user, organization=other_org).exists()
        )


@tag("batch_console_api")
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ConsoleUserEmailResendVerificationApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="resend-owner",
            email="resend-owner@example.com",
            password="password123",
        )
        self.client.force_login(self.user)
        self.url = reverse("console_user_email_resend_verification")

    def test_resend_sends_verification_email_for_unverified_primary_address(self):
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=False,
            primary=True,
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"verified": False, "message": "Verification email sent."},
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertIn("/accounts/confirm-email/", mail.outbox[0].body)

    @patch("api.services.email_verification.send_email_verification", return_value=False)
    def test_resend_reports_when_email_was_recently_sent(self, mock_send_email_verification):
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=False,
            primary=True,
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "verified": False,
                "message": "A verification email was already sent recently. Please check your inbox or try again later.",
            },
        )
        mock_send_email_verification.assert_called_once()

    @patch("api.services.email_verification.send_email_verification", side_effect=OSError("smtp.internal.local"))
    def test_resend_returns_generic_error_when_email_send_fails(self, mock_send_email_verification):
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=False,
            primary=True,
        )

        with patch("console.api_views.logger.exception") as mock_logger_exception:
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"error": "Failed to send verification email. Please try again later."},
        )
        self.assertNotIn("smtp.internal.local", response.content.decode("utf-8"))
        mock_send_email_verification.assert_called_once()
        mock_logger_exception.assert_called_once()

    def test_resend_does_not_send_when_email_is_already_verified(self):
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"verified": True, "message": "Email already verified."},
        )
        self.assertEqual(len(mail.outbox), 0)
