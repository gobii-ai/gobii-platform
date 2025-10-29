import base64
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings
from django.utils import timezone

from api.agent.avatar import maybe_schedule_agent_avatar
from api.agent.tasks.avatar import generate_agent_avatar_task
from api.models import BrowserUseAgent, PersistentAgent


def _make_base_image_file() -> Path:
    """Create a temporary PNG file to act as the fish base image."""
    png_bytes = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGMAAQAABQAB"
        b"JzQnCgAAAABJRU5ErkJggg=="
    )
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    tmp.write(png_bytes)
    tmp.flush()
    return Path(tmp.name)


@tag("batch_agent_avatar")
class AgentAvatarGenerationTests(TestCase):
    def setUp(self) -> None:
        User = get_user_model()
        self.user = User.objects.create_user(
            username="avatar-owner",
            email="avatar@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Avatar Browser",
        )

    def _create_agent(self, charter: str = "Handle inbound support requests") -> PersistentAgent:
        return PersistentAgent.objects.create(
            user=self.user,
            name=f"Persistent Agent {timezone.now().timestamp()}",
            charter=charter,
            browser_use_agent=self.browser_agent,
        )

    def test_maybe_schedule_agent_avatar_skips_without_charter(self) -> None:
        agent = self._create_agent(charter="  ")
        with patch("api.agent.avatar.generate_agent_avatar_task.delay") as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertFalse(scheduled)
        mocked_delay.assert_not_called()
        agent.refresh_from_db()
        self.assertIsNone(agent.avatar_generation_requested_at)

    def test_maybe_schedule_agent_avatar_enqueues_once(self) -> None:
        agent = self._create_agent()
        def _run_immediately(callback):
            callback()

        with patch("django.db.transaction.on_commit", side_effect=_run_immediately):
            with patch("api.agent.avatar.generate_agent_avatar_task.delay") as mocked_delay:
                scheduled_first = maybe_schedule_agent_avatar(agent)
                scheduled_second = maybe_schedule_agent_avatar(agent)

        self.assertTrue(scheduled_first)
        self.assertFalse(scheduled_second)
        mocked_delay.assert_called_once_with(str(agent.id))
        agent.refresh_from_db()
        self.assertIsNotNone(agent.avatar_generation_requested_at)

    def test_generate_agent_avatar_success(self) -> None:
        agent = self._create_agent()
        base_image = _make_base_image_file()

        try:
            with override_settings(AGENT_AVATAR_BASE_IMAGE_PATH=str(base_image)):
                with patch("api.agent.tasks.avatar.OpenAI") as mocked_openai:
                    mocked_client = mocked_openai.return_value
                    mocked_client.images.edit.return_value = SimpleNamespace(
                        data=[SimpleNamespace(b64_json=base64.b64encode(b"fake-image").decode())]
                    )
                    generate_agent_avatar_task.run(str(agent.id))
                    _, call_kwargs = mocked_client.images.edit.call_args
                    image_arg = call_kwargs.get("image")
                    if image_arg is None and mocked_client.images.edit.call_args.args:
                        image_arg = mocked_client.images.edit.call_args.args[1]
                    self.assertIsNotNone(image_arg)
                    self.assertEqual(getattr(image_arg, "name", None), base_image.name)
        finally:
            base_image.unlink(missing_ok=True)

        agent.refresh_from_db()
        self.assertTrue(agent.avatar_storage_path)
        self.assertIsNotNone(agent.avatar_generated_at)
        self.assertIsNone(agent.avatar_generation_requested_at)
