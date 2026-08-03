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
from api.services.agent_avatar_public import build_public_agent_avatar_thumbnail_url
from api.tasks.avatar_thumbnails import generate_agent_avatar_thumbnail_task
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


def _image_bytes(
    size: tuple[int, int] = (512, 384),
    color: tuple[int, ...] = (24, 96, 160),
    mode: str = "RGB",
) -> bytes:
    output = io.BytesIO()
    Image.new(mode, size, color).save(output, format="PNG")
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
            is_staff=True,
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

    def _save_avatar(self, name: str = "avatar.png", image_bytes: bytes | None = None) -> None:
        self.agent.avatar.save(
            name,
            ContentFile(image_bytes if image_bytes is not None else _image_bytes()),
            save=True,
        )
        self.agent.refresh_from_db()

    def test_thumbnail_endpoint_generates_cached_thumbnail(self):
        self._save_avatar()

        response = self.client.get(reverse("agent_avatar_thumbnail", kwargs={"pk": self.agent.id}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/webp")

        thumbnail_data = _response_bytes(response)
        with Image.open(io.BytesIO(thumbnail_data)) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.size, (AGENT_AVATAR_THUMBNAIL_SIZE, AGENT_AVATAR_THUMBNAIL_SIZE))

        thumbnail_name = _agent_avatar_thumbnail_name(self.agent.id, self.agent.get_avatar_thumbnail_version())
        self.assertTrue(thumbnail_name.endswith(".webp"))
        self.assertTrue(default_storage.exists(thumbnail_name))

        with (
            patch("api.services.agent_avatar_thumbnails.Image.open", side_effect=AssertionError("thumbnail regenerated")),
            patch.object(default_storage, "exists", side_effect=AssertionError("storage existence check")),
        ):
            cached_response = self.client.get(reverse("agent_avatar_thumbnail", kwargs={"pk": self.agent.id}))

        self.assertEqual(cached_response.status_code, 200)
        self.assertEqual(cached_response["Content-Type"], "image/webp")
        self.assertEqual(cached_response["Cache-Control"], "private, max-age=86400, immutable")
        self.assertTrue(_response_bytes(cached_response))

    def test_thumbnail_preserves_transparency_in_webp_output(self):
        self._save_avatar(
            image_bytes=_image_bytes(
                color=(24, 96, 160, 96),
                mode="RGBA",
            )
        )

        response = self.client.get(reverse("agent_avatar_thumbnail", kwargs={"pk": self.agent.id}))

        self.assertEqual(response.status_code, 200)
        with Image.open(io.BytesIO(_response_bytes(response))) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((64, 64))[3], 96)

    def test_thumbnail_task_is_idempotent_and_rejects_stale_version(self):
        self._save_avatar()
        version = self.agent.get_avatar_thumbnail_version()

        self.assertTrue(generate_agent_avatar_thumbnail_task(str(self.agent.id), version))
        with patch(
            "api.services.agent_avatar_thumbnails.Image.open",
            side_effect=AssertionError("thumbnail regenerated"),
        ):
            self.assertTrue(generate_agent_avatar_thumbnail_task(str(self.agent.id), version))

        PersistentAgent.objects.filter(id=self.agent.id).update(
            updated_at=timezone.now() + timedelta(minutes=1)
        )
        self.assertFalse(generate_agent_avatar_thumbnail_task(str(self.agent.id), version))

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

    def test_thumbnail_format_revision_rotates_private_url_and_public_token(self):
        self._save_avatar()
        original_version = self.agent.get_avatar_thumbnail_version()
        original_url = self.agent.get_avatar_thumbnail_url()
        original_public_url = build_public_agent_avatar_thumbnail_url(self.agent)

        with patch.object(PersistentAgent, "AVATAR_THUMBNAIL_FORMAT_REVISION", "webp-test-next"):
            self.assertNotEqual(self.agent.get_avatar_thumbnail_version(), original_version)
            self.assertNotEqual(self.agent.get_avatar_thumbnail_url(), original_url)
            self.assertNotEqual(build_public_agent_avatar_thumbnail_url(self.agent), original_public_url)

    def test_thumbnail_endpoint_returns_404_without_avatar(self):
        response = self.client.get(reverse("agent_avatar_thumbnail", kwargs={"pk": self.agent.id}))

        self.assertEqual(response.status_code, 404)

    @override_settings(PUBLIC_SITE_URL="https://app.example.test")
    def test_public_thumbnail_url_serves_anonymous_signed_thumbnail(self):
        self._save_avatar()

        public_url = build_public_agent_avatar_thumbnail_url(self.agent)

        self.assertTrue(public_url.startswith("https://app.example.test/public/agents/"))
        self.assertIn("/avatar/thumb/?token=", public_url)

        anonymous_client = Client()
        response = anonymous_client.get(public_url.removeprefix("https://app.example.test"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertEqual(response["Cache-Control"], "public, max-age=86400")
        self.assertTrue(_response_bytes(response))

    def test_public_thumbnail_url_rejects_invalid_token(self):
        self._save_avatar()

        response = Client().get(
            reverse("agent_avatar_public_thumbnail", kwargs={"pk": self.agent.id}),
            {"token": "invalid"},
        )

        self.assertEqual(response.status_code, 404)

    @override_settings(LEGACY_CONSOLE_PAGE_REDIRECTS_ENABLED=True)
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
        self.assertEqual(shell_response.status_code, 302)
        self.assertEqual(shell_response.url, f"/app/agents/{self.agent.id}")
