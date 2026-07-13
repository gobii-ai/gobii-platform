import json
import re
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
            {
                "email": "profile-owner@example.com",
                "isVerified": True,
                "pendingEmail": None,
            },
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
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ConsoleUserEmailApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="email-change-owner",
            email="email-change-owner@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        self.client.force_login(self.user)
        self.url = reverse("console_user_email")

    def test_requires_authentication(self):
        response = self.client_class().post(
            self.url,
            data=json.dumps({"email": "new-owner@example.com"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)

    def test_rejects_non_object_json(self):
        for payload in ([], "new-owner@example.com"):
            with self.subTest(payload=payload):
                response = self.client.post(
                    self.url,
                    data=json.dumps(payload),
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.content, b"JSON object expected")

    def test_change_keeps_verified_current_email_until_confirmation(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"email": "New-Owner@Example.com"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "email-change-owner@example.com")
        current = EmailAddress.objects.get(user=self.user, email=self.user.email)
        pending = EmailAddress.objects.get(user=self.user, email="new-owner@example.com")
        self.assertTrue(current.primary)
        self.assertTrue(current.verified)
        self.assertFalse(pending.primary)
        self.assertFalse(pending.verified)
        self.assertEqual(
            response.json()["emailVerification"],
            {
                "email": "email-change-owner@example.com",
                "isVerified": True,
                "pendingEmail": "new-owner@example.com",
            },
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new-owner@example.com"])
        self.assertIn("next=%2Fapp%2Fprofile", mail.outbox[0].body)

    def test_confirming_pending_email_switches_account_and_notifies_old_address(self):
        self.client.post(
            self.url,
            data=json.dumps({"email": "confirmed-owner@example.com"}),
            content_type="application/json",
        )
        confirmation_url = re.search(
            r"https?://[^\s]+/accounts/confirm-email/[^\s]+",
            mail.outbox[0].body,
        )
        self.assertIsNotNone(confirmation_url)

        response = self.client.get(confirmation_url.group(0))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/app/profile")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "confirmed-owner@example.com")
        addresses = list(EmailAddress.objects.filter(user=self.user))
        self.assertEqual(len(addresses), 1)
        self.assertEqual(addresses[0].email, "confirmed-owner@example.com")
        self.assertTrue(addresses[0].verified)
        self.assertTrue(addresses[0].primary)
        self.assertTrue(any(message.to == ["email-change-owner@example.com"] for message in mail.outbox[1:]))

        profile_response = self.client.get(reverse("console_user_profile"))
        self.assertEqual(
            profile_response.json()["emailVerification"],
            {
                "email": "confirmed-owner@example.com",
                "isVerified": True,
                "pendingEmail": None,
            },
        )

    def test_new_change_replaces_previous_pending_address(self):
        for email in ("first-pending@example.com", "second-pending@example.com"):
            response = self.client.post(
                self.url,
                data=json.dumps({"email": email}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

        self.assertFalse(
            EmailAddress.objects.filter(user=self.user, email="first-pending@example.com").exists()
        )
        self.assertTrue(
            EmailAddress.objects.filter(user=self.user, email="second-pending@example.com").exists()
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "email-change-owner@example.com")

    def test_cancel_removes_only_pending_address(self):
        self.client.post(
            self.url,
            data=json.dumps({"email": "cancel-me@example.com"}),
            content_type="application/json",
        )

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["emailVerification"]["pendingEmail"])
        self.assertFalse(
            EmailAddress.objects.filter(user=self.user, email="cancel-me@example.com").exists()
        )
        current = EmailAddress.objects.get(user=self.user, email="email-change-owner@example.com")
        self.assertTrue(current.verified)
        self.assertTrue(current.primary)

    def test_rejects_invalid_duplicate_and_blocked_addresses(self):
        other_user = get_user_model().objects.create_user(
            username="other-email-owner",
            email="already-used@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=other_user,
            email=other_user.email,
            verified=True,
            primary=True,
        )

        for email in (
            "not-an-email",
            self.user.email,
            "already-used@example.com",
            "blocked@mailslurp.biz",
        ):
            with self.subTest(email=email):
                response = self.client.post(
                    self.url,
                    data=json.dumps({"email": email}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("email", response.json()["errors"])

        self.assertEqual(EmailAddress.objects.filter(user=self.user).count(), 1)

    @patch("api.services.email_verification.send_email_verification", side_effect=OSError("smtp.internal.local"))
    def test_send_failure_restores_previous_pending_address(self, mock_send_email_verification):
        EmailAddress.objects.create(
            user=self.user,
            email="previous-pending@example.com",
            verified=False,
        )
        with patch("console.api_views.logger.exception") as mock_logger_exception:
            response = self.client.post(
                self.url,
                data=json.dumps({"email": "send-failure@example.com"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"error": "Failed to send verification email. Please try again later."},
        )
        self.assertFalse(
            EmailAddress.objects.filter(user=self.user, email="send-failure@example.com").exists()
        )
        self.assertTrue(
            EmailAddress.objects.filter(user=self.user, email="previous-pending@example.com").exists()
        )
        mock_send_email_verification.assert_called_once()
        mock_logger_exception.assert_called_once()

    def test_change_replaces_unverified_email_when_no_verified_identity_exists(self):
        EmailAddress.objects.filter(user=self.user).update(verified=False)

        response = self.client.post(
            self.url,
            data=json.dumps({"email": "corrected-owner@example.com"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "corrected-owner@example.com")
        address = EmailAddress.objects.get(user=self.user)
        self.assertEqual(address.email, "corrected-owner@example.com")
        self.assertTrue(address.primary)
        self.assertFalse(address.verified)
        self.assertEqual(
            response.json()["emailVerification"],
            {
                "email": "corrected-owner@example.com",
                "isVerified": False,
                "pendingEmail": None,
            },
        )

    def test_change_replaces_unverified_current_email_without_discarding_verified_history(self):
        EmailAddress.objects.filter(user=self.user).update(verified=False)
        EmailAddress.objects.create(
            user=self.user,
            email="old-verified@example.com",
            verified=True,
        )

        response = self.client.post(
            self.url,
            data=json.dumps({"email": "corrected-owner@example.com"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "corrected-owner@example.com")
        corrected = EmailAddress.objects.get(user=self.user, email=self.user.email)
        self.assertTrue(corrected.primary)
        self.assertFalse(corrected.verified)
        self.assertTrue(
            EmailAddress.objects.filter(
                user=self.user,
                email="old-verified@example.com",
                verified=True,
            ).exists()
        )
        self.assertEqual(
            response.json()["emailVerification"],
            {
                "email": "corrected-owner@example.com",
                "isVerified": False,
                "pendingEmail": None,
            },
        )


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
        self.url = reverse("console_user_email")

    def test_resend_sends_verification_email_for_unverified_primary_address(self):
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=False,
            primary=True,
        )

        response = self.client.put(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "emailVerification": {
                    "email": "resend-owner@example.com",
                    "isVerified": False,
                    "pendingEmail": None,
                },
            },
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertIn("/accounts/confirm-email/", mail.outbox[0].body)

    @patch("console.api_views.send_email_verification", return_value=False)
    def test_resend_reports_when_email_was_recently_sent(self, mock_send_email_verification):
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=False,
            primary=True,
        )

        response = self.client.put(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "emailVerification": {
                    "email": "resend-owner@example.com",
                    "isVerified": False,
                    "pendingEmail": None,
                },
            },
        )
        mock_send_email_verification.assert_called_once()

    @patch("console.api_views.send_email_verification", side_effect=OSError("smtp.internal.local"))
    def test_resend_returns_generic_error_when_email_send_fails(self, mock_send_email_verification):
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=False,
            primary=True,
        )

        with patch("console.api_views.logger.exception") as mock_logger_exception:
            response = self.client.put(self.url)

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

        response = self.client.put(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "emailVerification": {
                    "email": "resend-owner@example.com",
                    "isVerified": True,
                    "pendingEmail": None,
                },
            },
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_targets_pending_change_before_verified_current_address(self):
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        EmailAddress.objects.create(
            user=self.user,
            email="pending-resend@example.com",
            verified=False,
            primary=False,
        )

        response = self.client.put(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox[0].to, ["pending-resend@example.com"])
        self.assertEqual(
            response.json()["emailVerification"],
            {
                "email": "resend-owner@example.com",
                "isVerified": True,
                "pendingEmail": "pending-resend@example.com",
            },
        )
