import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.models import ProductAnnouncement, ProductAnnouncementRead


@tag("batch_console_api")
class ProductAnnouncementApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="announcement-user@example.com",
            email="announcement-user@example.com",
            password="password123",
        )
        self.other_user = user_model.objects.create_user(
            username="announcement-other@example.com",
            email="announcement-other@example.com",
            password="password123",
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.list_url = reverse("console_product_announcements")
        self.read_url = reverse("console_product_announcements_read")

    def _create_announcement(self, title: str, *, published_delta_minutes: int = 0, **overrides):
        defaults = {
            "title": title,
            "body": f"{title} body",
            "published_at": timezone.now() + timedelta(minutes=published_delta_minutes),
        }
        defaults.update(overrides)
        return ProductAnnouncement.objects.create(**defaults)

    def _post_read(self, payload):
        return self.client.post(
            self.read_url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_list_returns_recent_visible_announcements_with_unread_count(self):
        visible = [
            self._create_announcement(f"Visible {index}", published_delta_minutes=-index)
            for index in range(7)
        ]
        self._create_announcement("Inactive", is_active=False)
        self._create_announcement("Future", published_delta_minutes=10)
        self._create_announcement(
            "Expired",
            published_delta_minutes=-20,
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["recentLimit"], 5)
        self.assertEqual(payload["unreadCount"], len(visible))
        self.assertTrue(payload["hasUnread"])
        self.assertEqual(
            [entry["id"] for entry in payload["announcements"]],
            [str(announcement.id) for announcement in visible[:5]],
        )
        self.assertTrue(all(entry["isRead"] is False for entry in payload["announcements"]))

    def test_single_mark_read_is_idempotent_and_per_user(self):
        first = self._create_announcement("First")
        self._create_announcement("Second", published_delta_minutes=-1)

        first_response = self._post_read({"announcementIds": [str(first.id)]})
        second_response = self._post_read({"announcementIds": [str(first.id)]})

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(ProductAnnouncementRead.objects.filter(user=self.user).count(), 1)
        self.assertEqual(second_response.json()["unreadCount"], 1)
        first_entry = next(entry for entry in second_response.json()["announcements"] if entry["id"] == str(first.id))
        self.assertTrue(first_entry["isRead"])
        self.assertIsNotNone(first_entry["readAt"])

        other_client = Client()
        other_client.force_login(self.other_user)
        other_response = other_client.get(self.list_url)
        self.assertEqual(other_response.status_code, 200)
        self.assertEqual(other_response.json()["unreadCount"], 2)

    def test_mark_all_reads_all_visible_announcements_not_only_recent_five(self):
        visible = [
            self._create_announcement(f"Visible {index}", published_delta_minutes=-index)
            for index in range(7)
        ]
        hidden = self._create_announcement("Inactive", is_active=False)

        response = self._post_read({"all": True})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unreadCount"], 0)
        self.assertFalse(response.json()["hasUnread"])
        self.assertEqual(
            ProductAnnouncementRead.objects.filter(user=self.user).count(),
            len(visible),
        )
        self.assertFalse(
            ProductAnnouncementRead.objects.filter(user=self.user, announcement=hidden).exists()
        )
        self.assertTrue(all(entry["isRead"] for entry in response.json()["announcements"]))

    def test_endpoints_require_authentication(self):
        anonymous = Client()

        list_response = anonymous.get(self.list_url)
        read_response = anonymous.post(
            self.read_url,
            data=json.dumps({"all": True}),
            content_type="application/json",
        )

        self.assertEqual(list_response.status_code, 401)
        self.assertEqual(read_response.status_code, 401)

    def test_mark_read_rejects_invalid_payloads(self):
        invalid_payloads = [
            [],
            ["not-an-object"],
            "not-an-object",
            True,
            None,
            {},
            {"all": False},
            {"all": True, "announcementIds": [str(self._create_announcement("Bad Both").id)]},
            {"announcementIds": []},
            {"announcementIds": "not-a-list"},
            {"announcementIds": ["not-a-uuid"]},
            {"unknown": True},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self._post_read(payload)
                self.assertEqual(response.status_code, 400)

    def test_action_url_validation_allows_safe_urls_and_rejects_unsafe_urls(self):
        safe_path = ProductAnnouncement(
            title="Safe path",
            body="Body",
            action_label="Open",
            action_url="/app/agents",
        )
        safe_path.full_clean()

        safe_url = ProductAnnouncement(
            title="Safe URL",
            body="Body",
            action_label="Open",
            action_url="https://example.com/changelog",
        )
        safe_url.full_clean()

        unsafe_urls = [
            "javascript:alert(1)",
            "//example.com/phish",
            "/app\\evil",
            "mailto:support@example.com",
        ]
        for unsafe_url in unsafe_urls:
            with self.subTest(unsafe_url=unsafe_url):
                announcement = ProductAnnouncement(
                    title="Unsafe",
                    body="Body",
                    action_label="Open",
                    action_url=unsafe_url,
                )
                with self.assertRaises(ValidationError):
                    announcement.full_clean()
