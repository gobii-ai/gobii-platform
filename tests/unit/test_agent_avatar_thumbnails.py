import io
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import Client, TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from api.models import BrowserUseAgent, PersistentAgent
from console.views import AGENT_AVATAR_THUMBNAIL_SIZE, _agent_avatar_thumbnail_name


def _test_storages(media_root: str) -> dict:
    return {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": media_root, "base_url": "/media/"},
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }


def _image_bytes(size: tuple[int, int] = (512, 384), color: tuple[int, int, int] = (24, 96, 160)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def _response_bytes(response) -> bytes:
    return b"".join(response.streaming_content)


@tag("batch_agent_chat")
class AgentAvatarThumbnailTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.temp_media.name,
            STORAGES=_test_storages(self.temp_media.name),
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temp_media.cleanup)

        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="avatar-thumb-owner",
            email="avatar-thumb-owner@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Avatar Thumb Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Avatar Thumb Agent",
            charter="Test thumbnails",
            browser_use_agent=self.browser_agent,
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _save_avatar(self, name: str = "avatar.png") -> None:
        self.agent.avatar.save(name, ContentFile(_image_bytes()), save=True)
        self.agent.refresh_from_db()

    def test_thumbnail_endpoint_generates_cached_thumbnail(self):
        self._save_avatar()

        response = self.client.get(reverse("agent_avatar_thumbnail", kwargs={"pk": self.agent.id}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

        thumbnail_data = _response_bytes(response)
        with Image.open(io.BytesIO(thumbnail_data)) as image:
            self.assertLessEqual(image.width, AGENT_AVATAR_THUMBNAIL_SIZE)
            self.assertLessEqual(image.height, AGENT_AVATAR_THUMBNAIL_SIZE)

        thumbnail_name = _agent_avatar_thumbnail_name(self.agent.id, self.agent.get_avatar_thumbnail_version())
        self.assertTrue(default_storage.exists(thumbnail_name))

        with patch("console.views.Image.open", side_effect=AssertionError("thumbnail regenerated")):
            cached_response = self.client.get(reverse("agent_avatar_thumbnail", kwargs={"pk": self.agent.id}))

        self.assertEqual(cached_response.status_code, 200)
        self.assertEqual(cached_response["Content-Type"], "image/png")
        self.assertTrue(_response_bytes(cached_response))

    def test_thumbnail_url_and_cache_key_change_when_avatar_timestamp_changes(self):
        self._save_avatar()
        original_avatar_name = self.agent.avatar.name
        original_url = self.agent.get_avatar_thumbnail_url()
        original_thumbnail_name = _agent_avatar_thumbnail_name(self.agent.id, self.agent.get_avatar_thumbnail_version())

        PersistentAgent.objects.filter(id=self.agent.id).update(updated_at=timezone.now() + timedelta(minutes=1))
        self.agent.refresh_from_db()

        self.assertEqual(self.agent.avatar.name, original_avatar_name)
        self.assertNotEqual(self.agent.get_avatar_thumbnail_url(), original_url)
        self.assertNotEqual(
            _agent_avatar_thumbnail_name(self.agent.id, self.agent.get_avatar_thumbnail_version()),
            original_thumbnail_name,
        )

    def test_thumbnail_endpoint_returns_404_without_avatar(self):
        response = self.client.get(reverse("agent_avatar_thumbnail", kwargs={"pk": self.agent.id}))

        self.assertEqual(response.status_code, 404)

    def test_live_chat_payloads_use_thumbnail_urls(self):
        self._save_avatar()

        roster_response = self.client.get(reverse("console_agent_roster"))
        self.assertEqual(roster_response.status_code, 200)
        roster_payload = roster_response.json()
        roster_agent = next(agent for agent in roster_payload["agents"] if agent["id"] == str(self.agent.id))
        self.assertIn("/avatar/thumb/", roster_agent["avatar_url"])

        timeline_response = self.client.get(reverse("console_agent_timeline", kwargs={"agent_id": self.agent.id}))
        self.assertEqual(timeline_response.status_code, 200)
        self.assertIn("/avatar/thumb/", timeline_response.json()["agent_avatar_url"])

        shell_response = self.client.get(reverse("agent_chat_shell", kwargs={"pk": self.agent.id}))
        self.assertEqual(shell_response.status_code, 200)
        self.assertContains(shell_response, "/avatar/thumb/")
