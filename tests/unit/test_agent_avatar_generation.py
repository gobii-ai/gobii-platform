from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.avatar import maybe_schedule_agent_avatar
from api.agent.short_description import compute_charter_hash
from api.agent.tasks.agent_avatar import (
    AvatarGenerationResult,
    generate_agent_avatar_task,
    generate_agent_visual_description_task,
)
from api.models import BrowserUseAgent, PersistentAgent


@tag("batch_agent_short_description")
class AgentAvatarGenerationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="avatar-owner",
            email="avatar-owner@example.com",
            password="pass",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Avatar Browser")

    def _create_agent(self, charter: str = "Run technical recruiting workflows") -> PersistentAgent:
        return PersistentAgent.objects.create(
            user=self.user,
            name="Avatar Agent",
            charter=charter,
            browser_use_agent=self.browser_agent,
        )

    def test_maybe_schedule_agent_avatar_enqueues_visual_description(self):
        agent = self._create_agent()
        with patch(
            "api.agent.tasks.agent_avatar.generate_agent_visual_description_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertTrue(scheduled)
        expected_hash = compute_charter_hash(agent.charter)
        mocked_delay.assert_called_once_with(str(agent.id), expected_hash, None)
        agent.refresh_from_db()
        self.assertEqual(agent.visual_description_requested_hash, expected_hash)

    def test_maybe_schedule_agent_avatar_enqueues_avatar_when_visual_exists(self):
        agent = self._create_agent()
        agent.visual_description = "A composed professional with distinct freckles and wire-rim glasses."
        agent.save(update_fields=["visual_description"])

        with patch("api.agent.avatar.is_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertTrue(scheduled)
        expected_hash = compute_charter_hash(agent.charter)
        mocked_delay.assert_called_once_with(str(agent.id), expected_hash, None)
        agent.refresh_from_db()
        self.assertEqual(agent.avatar_requested_hash, expected_hash)

    def test_generate_visual_description_task_updates_fields_and_schedules_avatar(self):
        agent = self._create_agent()
        charter_hash = compute_charter_hash(agent.charter)
        agent.visual_description_requested_hash = charter_hash
        agent.save(update_fields=["visual_description_requested_hash"])

        with patch(
            "api.agent.tasks.agent_avatar._generate_visual_description_via_llm",
            return_value=(
                "A calm, mid-30s professional with short dark hair, olive skin, "
                "and a navy blazer who projects approachable confidence."
            ),
        ), patch(
            "api.agent.tasks.agent_avatar.maybe_schedule_agent_avatar",
            return_value=True,
        ) as mocked_schedule_avatar:
            generate_agent_visual_description_task.run(str(agent.id), charter_hash)

        agent.refresh_from_db()
        self.assertTrue(agent.visual_description)
        self.assertEqual(agent.visual_description_charter_hash, charter_hash)
        self.assertEqual(agent.visual_description_requested_hash, "")
        mocked_schedule_avatar.assert_called_once_with(agent, routing_profile_id=None)

    def test_generate_agent_avatar_task_updates_avatar_fields(self):
        agent = self._create_agent()
        charter_hash = compute_charter_hash(agent.charter)
        agent.visual_description = "A confident operator with sharp features and a tailored charcoal shirt."
        agent.avatar_requested_hash = charter_hash
        agent.save(update_fields=["visual_description", "avatar_requested_hash"])

        with patch(
            "api.agent.tasks.agent_avatar._generate_avatar_image",
            return_value=AvatarGenerationResult(
                image_bytes=b"fake-image-bytes",
                mime_type="image/png",
                endpoint_key="test-endpoint",
                model="test-model",
                error_detail=None,
            ),
        ):
            generate_agent_avatar_task.run(str(agent.id), charter_hash)

        agent.refresh_from_db()
        self.assertTrue(agent.avatar)
        self.assertEqual(agent.avatar_charter_hash, charter_hash)
        self.assertEqual(agent.avatar_requested_hash, "")

    def test_generate_agent_avatar_task_skips_when_charter_changes(self):
        agent = self._create_agent()
        old_hash = compute_charter_hash(agent.charter)
        agent.visual_description = "Distinctive profile"
        agent.avatar_requested_hash = old_hash
        agent.save(update_fields=["visual_description", "avatar_requested_hash"])

        agent.charter = "Handle post-sales customer success workflows"
        agent.save(update_fields=["charter"])

        with patch("api.agent.tasks.agent_avatar._generate_avatar_image") as mocked_image_generation:
            generate_agent_avatar_task.run(str(agent.id), old_hash)

        agent.refresh_from_db()
        self.assertFalse(agent.avatar)
        self.assertEqual(agent.avatar_requested_hash, "")
        mocked_image_generation.assert_not_called()
