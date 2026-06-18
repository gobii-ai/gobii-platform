import json
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from api.models import AgentOwnerCustomInstructions, UserPhoneNumber, UserPreference


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
