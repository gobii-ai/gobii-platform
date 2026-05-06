import json

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.models import UserPhoneNumber, UserPreference


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
