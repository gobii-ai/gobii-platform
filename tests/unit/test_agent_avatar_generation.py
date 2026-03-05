from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.agent.avatar import maybe_schedule_agent_avatar
from api.agent.short_description import compute_charter_hash
from api.agent.tasks.agent_avatar import (
    AvatarGenerationResult,
    generate_agent_avatar_task,
    generate_agent_visual_description_task,
)
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentCompletion, SystemSetting
from api.tasks.avatar_backfill import schedule_agent_avatar_backfill_task


@tag("batch_agent_short_description")
class AgentAvatarGenerationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="avatar-owner",
            email="avatar-owner@example.com",
            password="pass",
        )

    def _create_agent(self, charter: str = "Run technical recruiting workflows") -> PersistentAgent:
        sequence = PersistentAgent.objects.count() + 1
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name=f"Avatar Browser {sequence}",
        )
        return PersistentAgent.objects.create(
            user=self.user,
            name=f"Avatar Agent {sequence}",
            charter=charter,
            browser_use_agent=browser_agent,
        )

    def _prepare_visual_ready_agent(self, charter: str = "Run technical recruiting workflows") -> PersistentAgent:
        agent = self._create_agent(charter=charter)
        agent.visual_description = "A composed professional with warm expression."
        agent.save(update_fields=["visual_description"])
        return agent

    def _set_avatar_attempt_time(self, agent: PersistentAgent, *, hours_ago: int) -> None:
        agent.avatar_last_generation_attempt_at = timezone.now() - timedelta(hours=hours_ago)
        agent.save(update_fields=["avatar_last_generation_attempt_at"])

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

    def test_maybe_schedule_agent_avatar_does_not_duplicate_visual_request_when_db_is_pending(self):
        agent = self._create_agent()
        charter_hash = compute_charter_hash(agent.charter)
        PersistentAgent.objects.filter(id=agent.id).update(
            visual_description_requested_hash=charter_hash,
        )

        with patch(
            "api.agent.tasks.agent_avatar.generate_agent_visual_description_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertFalse(scheduled)
        mocked_delay.assert_not_called()

    @override_settings(AGENT_AVATAR_GENERATION_COOLDOWN_HOURS=24)
    def test_maybe_schedule_agent_avatar_respects_cooldown(self):
        agent = self._prepare_visual_ready_agent()
        self._set_avatar_attempt_time(agent, hours_ago=3)

        with patch("api.agent.avatar.is_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertFalse(scheduled)
        mocked_delay.assert_not_called()

    @override_settings(AGENT_AVATAR_GENERATION_COOLDOWN_HOURS=24)
    def test_maybe_schedule_agent_avatar_allows_enqueue_after_cooldown(self):
        agent = self._prepare_visual_ready_agent()
        self._set_avatar_attempt_time(agent, hours_ago=25)

        with patch("api.agent.avatar.is_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertTrue(scheduled)
        expected_hash = compute_charter_hash(agent.charter)
        mocked_delay.assert_called_once_with(str(agent.id), expected_hash, None)

    def test_maybe_schedule_agent_avatar_skips_when_any_avatar_request_is_pending(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar_requested_hash = "pending-other-hash"
        agent.save(update_fields=["avatar_requested_hash"])

        with patch("api.agent.avatar.is_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertFalse(scheduled)
        mocked_delay.assert_not_called()

    def test_generate_agent_avatar_task_records_attempt_and_logs_each_endpoint_attempt(self):
        agent = self._create_agent()
        charter_hash = compute_charter_hash(agent.charter)
        agent.visual_description = "A confident operator with sharp features and a tailored charcoal shirt."
        agent.avatar_requested_hash = charter_hash
        agent.save(update_fields=["visual_description", "avatar_requested_hash"])

        first_error = ValueError("endpoint one failed")
        first_error.response = {"id": "resp-fail"}
        generated = SimpleNamespace(
            image_bytes=b"fake-image-bytes",
            mime_type="image/png",
            response={"id": "resp-success"},
        )
        configs = [
            SimpleNamespace(endpoint_key="first", model="openai/gpt-image-1"),
            SimpleNamespace(endpoint_key="second", model="anthropic/image-foo"),
        ]

        with patch("api.agent.tasks.agent_avatar.get_image_generation_llm_configs", return_value=configs), patch(
            "api.agent.tasks.agent_avatar._generate_image_bytes",
            side_effect=[first_error, generated],
        ):
            generate_agent_avatar_task.run(str(agent.id), charter_hash)

        agent.refresh_from_db()
        self.assertIsNotNone(agent.avatar_last_generation_attempt_at)
        self.assertTrue(agent.avatar)
        self.assertEqual(agent.avatar_requested_hash, "")
        completions = list(
            PersistentAgentCompletion.objects.filter(
                agent=agent,
                completion_type=PersistentAgentCompletion.CompletionType.AVATAR_IMAGE_GENERATION,
            ).order_by("created_at")
        )
        self.assertEqual(len(completions), 2)
        self.assertEqual([completion.llm_model for completion in completions], [configs[0].model, configs[1].model])

    @patch("api.tasks.avatar_backfill.is_image_generation_configured", return_value=True)
    def test_schedule_agent_avatar_backfill_task_limits_and_advances_cursor(self, _mock_image_ready):
        agents = [
            self._create_agent("Charter one"),
            self._create_agent("Charter two"),
            self._create_agent("Charter three"),
        ]
        ordered_ids = list(
            PersistentAgent.objects.filter(id__in=[agent.id for agent in agents])
            .order_by("id")
            .values_list("id", flat=True)
        )

        with patch(
            "api.tasks.avatar_backfill.maybe_schedule_agent_avatar",
            return_value=True,
        ) as mocked_schedule:
            first = schedule_agent_avatar_backfill_task(batch_size=2, scan_limit=2)

        self.assertEqual(first, 2)
        first_call_ids = [call.args[0].id for call in mocked_schedule.call_args_list]
        self.assertEqual(first_call_ids, ordered_ids[:2])

        cursor_after_first = SystemSetting.objects.get(key="AGENT_AVATAR_BACKFILL_CURSOR")
        self.assertEqual(cursor_after_first.value_text, str(ordered_ids[1]))

        with patch(
            "api.tasks.avatar_backfill.maybe_schedule_agent_avatar",
            return_value=True,
        ) as mocked_schedule:
            second = schedule_agent_avatar_backfill_task(batch_size=2, scan_limit=2)

        self.assertEqual(second, 1)
        second_call_ids = [call.args[0].id for call in mocked_schedule.call_args_list]
        self.assertEqual(second_call_ids, [ordered_ids[2]])

    @override_settings(AGENT_AVATAR_BACKFILL_ENABLED=False)
    @patch("api.tasks.avatar_backfill.maybe_schedule_agent_avatar")
    def test_schedule_agent_avatar_backfill_task_skips_when_disabled(self, mocked_schedule):
        self._create_agent("Charter one")

        scheduled = schedule_agent_avatar_backfill_task(batch_size=5, scan_limit=5)

        self.assertEqual(scheduled, 0)
        mocked_schedule.assert_not_called()

    @patch("api.tasks.avatar_backfill.is_image_generation_configured", return_value=True)
    @patch("api.tasks.avatar_backfill.maybe_schedule_agent_avatar")
    def test_schedule_agent_avatar_backfill_task_skips_pending_agents(
        self,
        mocked_schedule,
        _mock_image_ready,
    ):
        pending_agent = self._create_agent("Pending charter")
        pending_agent.avatar_requested_hash = "pending"
        pending_agent.save(update_fields=["avatar_requested_hash"])

        scheduled = schedule_agent_avatar_backfill_task(batch_size=5, scan_limit=5)

        self.assertEqual(scheduled, 0)
        mocked_schedule.assert_not_called()
