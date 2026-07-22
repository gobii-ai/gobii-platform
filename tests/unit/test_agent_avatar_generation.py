from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.agent.avatar import compute_appearance_revision, maybe_schedule_agent_avatar
from api.agent.short_description import compute_charter_hash
from api.agent.tasks.agent_avatar import (
    AvatarGenerationResult,
    _generate_avatar_image,
    generate_agent_avatar_task,
    generate_agent_visual_description_task,
)
from api.models import (
    BrowserUseAgent,
    ImageGenerationLLMTier,
    ImageGenerationModelEndpoint,
    ImageGenerationTierEndpoint,
    LLMProvider,
    PersistentAgent,
    PersistentAgentCompletion,
    SystemSetting,
)
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

    def _create_agent(
        self,
        charter: str = "Run technical recruiting workflows",
        *,
        execution_environment: str | None = None,
    ) -> PersistentAgent:
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
            **({"execution_environment": execution_environment} if execution_environment else {}),
        )

    def _prepare_visual_ready_agent(self, charter: str = "Run technical recruiting workflows") -> PersistentAgent:
        agent = self._create_agent(charter=charter)
        agent.visual_description = "A composed professional with warm expression."
        agent.save(update_fields=["visual_description"])
        return agent

    def _set_avatar_attempt_time(self, agent: PersistentAgent, *, hours_ago: int) -> None:
        agent.avatar_last_generation_attempt_at = timezone.now() - timedelta(hours=hours_ago)
        agent.save(update_fields=["avatar_last_generation_attempt_at"])

    def _seed_image_generation_tier(self, *, use_case: str, endpoint_key: str) -> None:
        provider = LLMProvider.objects.create(
            key=f"provider-{endpoint_key}",
            display_name=f"Provider {endpoint_key}",
            enabled=True,
        )
        endpoint = ImageGenerationModelEndpoint.objects.create(
            key=endpoint_key,
            provider=provider,
            enabled=True,
            litellm_model=f"{endpoint_key}-model",
            api_base="https://example.com/v1",
        )
        tier = ImageGenerationLLMTier.objects.create(
            use_case=use_case,
            order=1,
            description=f"{use_case} tier",
        )
        ImageGenerationTierEndpoint.objects.create(
            tier=tier,
            endpoint=endpoint,
            weight=1.0,
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

    def test_maybe_schedule_agent_avatar_skips_eval_agent(self):
        agent = self._create_agent(execution_environment="eval")
        with patch(
            "api.agent.tasks.agent_avatar.generate_agent_visual_description_task.delay"
        ) as mocked_visual_delay, patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_avatar_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertFalse(scheduled)
        mocked_visual_delay.assert_not_called()
        mocked_avatar_delay.assert_not_called()
        agent.refresh_from_db()
        self.assertEqual(agent.visual_description_requested_hash, "")
        self.assertEqual(agent.avatar_requested_hash, "")

    def test_maybe_schedule_agent_avatar_enqueues_avatar_when_visual_exists(self):
        agent = self._create_agent()
        agent.visual_description = "A composed professional with distinct freckles and wire-rim glasses."
        agent.save(update_fields=["visual_description"])

        with patch("api.agent.avatar.is_avatar_image_generation_configured", return_value=True), patch(
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

    def test_visual_description_task_does_not_overwrite_owner_appearance_set_in_flight(self):
        agent = self._create_agent()
        charter_hash = compute_charter_hash(agent.charter)
        agent.visual_description_requested_hash = charter_hash
        agent.save(update_fields=["visual_description_requested_hash"])
        owner_appearance = "A woman with silver curls, amber eyes, and a forest-green jacket."

        def owner_updates_while_generation_runs(*_args):
            PersistentAgent.objects.filter(id=agent.id).update(
                visual_description=owner_appearance,
                visual_description_charter_hash=charter_hash,
                visual_description_requested_hash="",
            )
            return "A stale generated identity that must not win."

        with patch(
            "api.agent.tasks.agent_avatar._generate_visual_description_via_llm",
            side_effect=owner_updates_while_generation_runs,
        ), patch(
            "api.agent.tasks.agent_avatar.maybe_schedule_agent_avatar",
        ) as mocked_schedule_avatar:
            generate_agent_visual_description_task.run(str(agent.id), charter_hash)

        agent.refresh_from_db()
        self.assertEqual(agent.visual_description, owner_appearance)
        self.assertEqual(agent.visual_description_charter_hash, charter_hash)
        self.assertEqual(agent.visual_description_requested_hash, "")
        mocked_schedule_avatar.assert_not_called()

    def test_generate_visual_description_task_skips_eval_agent_and_clears_request(self):
        agent = self._create_agent(execution_environment="eval")
        charter_hash = compute_charter_hash(agent.charter)
        agent.visual_description_requested_hash = charter_hash
        agent.save(update_fields=["visual_description_requested_hash"])

        with patch("api.agent.tasks.agent_avatar._generate_visual_description_via_llm") as mocked_generate, patch(
            "api.agent.tasks.agent_avatar.maybe_schedule_agent_avatar"
        ) as mocked_schedule_avatar:
            generate_agent_visual_description_task.run(str(agent.id), charter_hash)

        agent.refresh_from_db()
        self.assertEqual(agent.visual_description, "")
        self.assertEqual(agent.visual_description_requested_hash, "")
        mocked_generate.assert_not_called()
        mocked_schedule_avatar.assert_not_called()

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

    def test_generate_agent_avatar_task_skips_eval_agent_and_clears_request(self):
        agent = self._create_agent(execution_environment="eval")
        charter_hash = compute_charter_hash(agent.charter)
        agent.visual_description = "A confident operator with sharp features and a tailored charcoal shirt."
        agent.avatar_requested_hash = charter_hash
        agent.save(update_fields=["visual_description", "avatar_requested_hash"])

        with patch("api.agent.tasks.agent_avatar._generate_avatar_image") as mocked_image_generation:
            generate_agent_avatar_task.run(str(agent.id), charter_hash)

        agent.refresh_from_db()
        self.assertFalse(agent.avatar)
        self.assertEqual(agent.avatar_requested_hash, "")
        mocked_image_generation.assert_not_called()

    def test_generate_agent_avatar_task_skips_when_avatar_already_exists(self):
        agent = self._create_agent()
        charter_hash = compute_charter_hash(agent.charter)
        agent.visual_description = "A confident operator with sharp features and a tailored charcoal shirt."
        agent.avatar_requested_hash = charter_hash
        agent.avatar.save("existing-avatar.png", ContentFile(b"existing-avatar"), save=False)
        agent.save(update_fields=["visual_description", "avatar_requested_hash", "avatar"])

        with patch("api.agent.tasks.agent_avatar._generate_avatar_image") as mocked_image_generation:
            generate_agent_avatar_task.run(str(agent.id), charter_hash)

        agent.refresh_from_db()
        self.assertEqual(agent.avatar_requested_hash, "")
        self.assertTrue(agent.avatar)
        mocked_image_generation.assert_not_called()

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

        with patch("api.agent.avatar.is_avatar_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertFalse(scheduled)
        mocked_delay.assert_not_called()

    @override_settings(AGENT_AVATAR_GENERATION_COOLDOWN_HOURS=24)
    def test_maybe_schedule_agent_avatar_allows_enqueue_after_cooldown(self):
        agent = self._prepare_visual_ready_agent()
        self._set_avatar_attempt_time(agent, hours_ago=25)

        with patch("api.agent.avatar.is_avatar_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertTrue(scheduled)
        expected_hash = compute_charter_hash(agent.charter)
        mocked_delay.assert_called_once_with(str(agent.id), expected_hash, None)

    def test_maybe_schedule_agent_avatar_skips_when_current_hash_is_already_pending(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar_requested_hash = compute_charter_hash(agent.charter)
        agent.save(update_fields=["avatar_requested_hash"])

        with patch("api.agent.avatar.is_avatar_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertFalse(scheduled)
        mocked_delay.assert_not_called()

    def test_maybe_schedule_agent_avatar_skips_when_avatar_already_exists(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar.save("existing-avatar.png", ContentFile(b"avatar-bytes"), save=False)
        agent.save(update_fields=["avatar"])

        with patch("api.agent.avatar.is_avatar_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertFalse(scheduled)
        mocked_delay.assert_not_called()

    @override_settings(AGENT_AVATAR_GENERATION_COOLDOWN_HOURS=24)
    def test_appearance_change_schedules_existing_avatar_despite_cooldown(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar.save("existing-avatar.png", ContentFile(b"avatar-bytes"), save=False)
        agent.avatar_last_generation_attempt_at = timezone.now()
        agent.save(update_fields=["avatar", "avatar_last_generation_attempt_at"])
        revision = compute_appearance_revision(agent.charter, agent.visual_description)

        with patch("api.agent.avatar.is_avatar_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent, appearance_changed=True)

        self.assertTrue(scheduled)
        mocked_delay.assert_called_once_with(
            str(agent.id),
            compute_charter_hash(agent.charter),
            None,
            revision,
        )
        agent.refresh_from_db()
        self.assertEqual(agent.avatar_requested_hash, revision)

    def test_appearance_change_deduplicates_current_pending_revision(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar.save("existing-avatar.png", ContentFile(b"avatar-bytes"), save=False)
        agent.save(update_fields=["avatar"])

        with patch("api.agent.avatar.is_avatar_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            first = maybe_schedule_agent_avatar(agent, appearance_changed=True)
            second = maybe_schedule_agent_avatar(agent, appearance_changed=True)

        self.assertTrue(first)
        self.assertFalse(second)
        mocked_delay.assert_called_once()

    def test_appearance_change_does_not_override_newer_manual_avatar_state(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar.save("existing-avatar.png", ContentFile(b"old-avatar"), save=False)
        agent.save(update_fields=["avatar"])
        expected_state = (
            agent.avatar.name,
            agent.avatar_charter_hash,
            agent.avatar_requested_hash,
        )
        manual_hash = compute_charter_hash(agent.charter)
        PersistentAgent.objects.filter(id=agent.id).update(
            avatar="agent_avatars/manual-avatar.png",
            avatar_charter_hash=manual_hash,
            avatar_requested_hash="",
        )

        with patch("api.agent.avatar.is_avatar_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(
                agent,
                appearance_changed=True,
                expected_avatar_state=expected_state,
            )

        self.assertFalse(scheduled)
        mocked_delay.assert_not_called()
        agent.refresh_from_db()
        self.assertEqual(agent.avatar.name, "agent_avatars/manual-avatar.png")
        self.assertEqual(agent.avatar_requested_hash, "")

    def test_appearance_revision_normalizes_whitespace_and_tracks_charter(self):
        appearance = "A calm operator with dark curls and green eyes."

        self.assertEqual(
            compute_appearance_revision("  Coordinate operations  ", f" A calm  operator\nwith dark curls and green eyes. "),
            compute_appearance_revision("Coordinate operations", appearance),
        )
        self.assertNotEqual(
            compute_appearance_revision("Coordinate operations", appearance),
            compute_appearance_revision("Lead customer success", appearance),
        )

    def test_maybe_schedule_agent_avatar_skips_when_avatar_was_saved_after_agent_loaded(self):
        current_agent = self._prepare_visual_ready_agent()
        stale_agent = PersistentAgent.objects.get(id=current_agent.id)
        current_agent.avatar.save("existing-avatar.png", ContentFile(b"avatar-bytes"), save=False)
        current_agent.save(update_fields=["avatar"])

        with patch("api.agent.avatar.is_avatar_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(stale_agent)

        self.assertFalse(scheduled)
        mocked_delay.assert_not_called()

    def test_maybe_schedule_agent_avatar_replaces_outdated_pending_hash(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar_requested_hash = compute_charter_hash("old charter")
        agent.save(update_fields=["avatar_requested_hash"])

        with patch("api.agent.avatar.is_avatar_image_generation_configured", return_value=True), patch(
            "api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay"
        ) as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertTrue(scheduled)
        expected_hash = compute_charter_hash(agent.charter)
        mocked_delay.assert_called_once_with(str(agent.id), expected_hash, None)
        agent.refresh_from_db()
        self.assertEqual(agent.avatar_requested_hash, expected_hash)

    def test_appearance_refresh_replaces_existing_avatar(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar.save("existing-avatar.png", ContentFile(b"old-avatar"), save=False)
        revision = compute_appearance_revision(agent.charter, agent.visual_description)
        agent.avatar_requested_hash = revision
        agent.save(update_fields=["avatar", "avatar_requested_hash"])
        old_avatar_name = agent.avatar.name

        with patch(
            "api.agent.tasks.agent_avatar._generate_avatar_image",
            return_value=AvatarGenerationResult(
                image_bytes=b"new-avatar",
                mime_type="image/png",
                endpoint_key="test-endpoint",
                model="test-model",
                error_detail=None,
            ),
        ):
            generate_agent_avatar_task.run(
                str(agent.id),
                compute_charter_hash(agent.charter),
                None,
                revision,
            )

        agent.refresh_from_db()
        self.assertTrue(agent.avatar)
        self.assertNotEqual(agent.avatar.name, old_avatar_name)
        self.assertEqual(agent.avatar_charter_hash, revision)
        self.assertEqual(agent.avatar_requested_hash, "")

    def test_failed_appearance_refresh_preserves_existing_avatar(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar.save("existing-avatar.png", ContentFile(b"old-avatar"), save=False)
        revision = compute_appearance_revision(agent.charter, agent.visual_description)
        agent.avatar_requested_hash = revision
        agent.save(update_fields=["avatar", "avatar_requested_hash"])
        old_avatar_name = agent.avatar.name

        with patch(
            "api.agent.tasks.agent_avatar._generate_avatar_image",
            return_value=AvatarGenerationResult(
                image_bytes=None,
                mime_type=None,
                endpoint_key=None,
                model=None,
                error_detail="generation failed",
            ),
        ):
            generate_agent_avatar_task.run(
                str(agent.id),
                compute_charter_hash(agent.charter),
                None,
                revision,
            )

        agent.refresh_from_db()
        self.assertEqual(agent.avatar.name, old_avatar_name)
        self.assertEqual(agent.avatar_requested_hash, "")

    def test_appearance_refresh_reschedules_when_charter_changed_before_task_start(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar.save("existing-avatar.png", ContentFile(b"old-avatar"), save=False)
        old_charter_hash = compute_charter_hash(agent.charter)
        revision = compute_appearance_revision(agent.charter, agent.visual_description)
        agent.avatar_requested_hash = revision
        agent.save(update_fields=["avatar", "avatar_requested_hash"])
        old_avatar_name = agent.avatar.name
        PersistentAgent.objects.filter(id=agent.id).update(charter="Lead customer research")

        with patch(
            "api.agent.tasks.agent_avatar.maybe_schedule_agent_avatar",
        ) as mocked_schedule_avatar:
            generate_agent_avatar_task.run(str(agent.id), old_charter_hash, None, revision)

        agent.refresh_from_db()
        self.assertEqual(agent.avatar.name, old_avatar_name)
        self.assertEqual(agent.avatar_requested_hash, "")
        self.assertEqual(mocked_schedule_avatar.call_count, 1)
        rescheduled_agent = mocked_schedule_avatar.call_args.args[0]
        self.assertEqual(rescheduled_agent.id, agent.id)
        self.assertEqual(rescheduled_agent.charter, "Lead customer research")
        self.assertTrue(mocked_schedule_avatar.call_args.kwargs["appearance_changed"])

    def test_appearance_refresh_does_not_undo_manual_clear_during_charter_change(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar.save("existing-avatar.png", ContentFile(b"old-avatar"), save=False)
        old_charter_hash = compute_charter_hash(agent.charter)
        revision = compute_appearance_revision(agent.charter, agent.visual_description)
        agent.avatar_requested_hash = revision
        agent.save(update_fields=["avatar", "avatar_requested_hash"])

        new_charter = "Lead customer research"
        PersistentAgent.objects.filter(id=agent.id).update(
            charter=new_charter,
            avatar=None,
            avatar_charter_hash=compute_charter_hash(new_charter),
            avatar_requested_hash="",
        )

        with patch(
            "api.agent.tasks.agent_avatar.maybe_schedule_agent_avatar",
        ) as mocked_schedule_avatar:
            generate_agent_avatar_task.run(str(agent.id), old_charter_hash, None, revision)

        agent.refresh_from_db()
        self.assertFalse(agent.avatar)
        self.assertEqual(agent.avatar_charter_hash, compute_charter_hash(new_charter))
        mocked_schedule_avatar.assert_not_called()

    def test_appearance_refresh_reschedules_when_charter_changes_during_render(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar.save("existing-avatar.png", ContentFile(b"old-avatar"), save=False)
        old_charter_hash = compute_charter_hash(agent.charter)
        revision = compute_appearance_revision(agent.charter, agent.visual_description)
        agent.avatar_requested_hash = revision
        agent.save(update_fields=["avatar", "avatar_requested_hash"])
        old_avatar_name = agent.avatar.name

        def generate_after_charter_change(_agent, _prompt):
            PersistentAgent.objects.filter(id=agent.id).update(charter="Lead customer research")
            return AvatarGenerationResult(
                image_bytes=b"stale-avatar",
                mime_type="image/png",
                endpoint_key="test-endpoint",
                model="test-model",
                error_detail=None,
            )

        with patch(
            "api.agent.tasks.agent_avatar._generate_avatar_image",
            side_effect=generate_after_charter_change,
        ), patch(
            "api.agent.tasks.agent_avatar.maybe_schedule_agent_avatar",
        ) as mocked_schedule_avatar:
            generate_agent_avatar_task.run(str(agent.id), old_charter_hash, None, revision)

        agent.refresh_from_db()
        self.assertEqual(agent.avatar.name, old_avatar_name)
        self.assertEqual(agent.avatar_requested_hash, "")
        self.assertEqual(mocked_schedule_avatar.call_count, 1)
        self.assertEqual(mocked_schedule_avatar.call_args.args[0].charter, "Lead customer research")
        self.assertTrue(mocked_schedule_avatar.call_args.kwargs["appearance_changed"])

    def test_appearance_refresh_discards_result_after_newer_appearance(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar.save("existing-avatar.png", ContentFile(b"old-avatar"), save=False)
        revision = compute_appearance_revision(agent.charter, agent.visual_description)
        agent.avatar_requested_hash = revision
        agent.save(update_fields=["avatar", "avatar_requested_hash"])
        old_avatar_name = agent.avatar.name
        newer_appearance = "A relaxed field researcher with silver hair and green eyes."
        newer_revision = compute_appearance_revision(agent.charter, newer_appearance)

        def generate_after_new_request(_agent, _prompt):
            PersistentAgent.objects.filter(id=agent.id).update(
                visual_description=newer_appearance,
                avatar_requested_hash=newer_revision,
            )
            return AvatarGenerationResult(
                image_bytes=b"stale-avatar",
                mime_type="image/png",
                endpoint_key="test-endpoint",
                model="test-model",
                error_detail=None,
            )

        with patch(
            "api.agent.tasks.agent_avatar._generate_avatar_image",
            side_effect=generate_after_new_request,
        ):
            generate_agent_avatar_task.run(
                str(agent.id),
                compute_charter_hash(agent.charter),
                None,
                revision,
            )

        agent.refresh_from_db()
        self.assertEqual(agent.avatar.name, old_avatar_name)
        self.assertEqual(agent.avatar_requested_hash, newer_revision)
        self.assertEqual(agent.visual_description, newer_appearance)

    def test_appearance_refresh_does_not_overwrite_later_manual_upload(self):
        agent = self._prepare_visual_ready_agent()
        agent.avatar.save("existing-avatar.png", ContentFile(b"old-avatar"), save=False)
        revision = compute_appearance_revision(agent.charter, agent.visual_description)
        agent.avatar_requested_hash = revision
        agent.save(update_fields=["avatar", "avatar_requested_hash"])

        def generate_after_manual_upload(_agent, _prompt):
            current = PersistentAgent.objects.get(id=agent.id)
            current.avatar.save("manual-avatar.png", ContentFile(b"manual-avatar"), save=False)
            current.avatar_requested_hash = ""
            current.save(update_fields=["avatar", "avatar_requested_hash"])
            return AvatarGenerationResult(
                image_bytes=b"generated-avatar",
                mime_type="image/png",
                endpoint_key="test-endpoint",
                model="test-model",
                error_detail=None,
            )

        with patch(
            "api.agent.tasks.agent_avatar._generate_avatar_image",
            side_effect=generate_after_manual_upload,
        ):
            generate_agent_avatar_task.run(
                str(agent.id),
                compute_charter_hash(agent.charter),
                None,
                revision,
            )

        agent.refresh_from_db()
        self.assertIn("manual-avatar", agent.avatar.name)
        self.assertEqual(agent.avatar_requested_hash, "")

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

        with patch("api.agent.tasks.agent_avatar.get_avatar_image_generation_llm_configs", return_value=configs), patch(
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

    def test_generate_avatar_image_prefers_avatar_specific_tiers(self):
        agent = self._create_agent()
        self._seed_image_generation_tier(
            use_case=ImageGenerationLLMTier.UseCase.CREATE_IMAGE,
            endpoint_key="create-image-endpoint",
        )
        self._seed_image_generation_tier(
            use_case=ImageGenerationLLMTier.UseCase.AVATAR,
            endpoint_key="avatar-endpoint",
        )
        generated = SimpleNamespace(
            image_bytes=b"avatar-bytes",
            mime_type="image/png",
            response={"id": "resp-avatar"},
        )

        with patch(
            "api.agent.tasks.agent_avatar._generate_image_bytes",
            return_value=generated,
        ) as mocked_generate:
            result = _generate_avatar_image(agent, "Generate an avatar")

        self.assertEqual(result.endpoint_key, "avatar-endpoint")
        self.assertEqual(mocked_generate.call_args.args[0].endpoint_key, "avatar-endpoint")

    def test_generate_avatar_image_falls_back_to_create_image_tiers(self):
        agent = self._create_agent()
        self._seed_image_generation_tier(
            use_case=ImageGenerationLLMTier.UseCase.CREATE_IMAGE,
            endpoint_key="create-image-endpoint",
        )
        generated = SimpleNamespace(
            image_bytes=b"avatar-bytes",
            mime_type="image/png",
            response={"id": "resp-create"},
        )

        with patch(
            "api.agent.tasks.agent_avatar._generate_image_bytes",
            return_value=generated,
        ) as mocked_generate:
            result = _generate_avatar_image(agent, "Generate an avatar")

        self.assertEqual(result.endpoint_key, "create-image-endpoint")
        self.assertEqual(mocked_generate.call_args.args[0].endpoint_key, "create-image-endpoint")

    def test_maybe_schedule_agent_avatar_uses_create_image_fallback_when_avatar_tiers_absent(self):
        agent = self._prepare_visual_ready_agent()
        self._seed_image_generation_tier(
            use_case=ImageGenerationLLMTier.UseCase.CREATE_IMAGE,
            endpoint_key="create-image-endpoint",
        )

        with patch("api.agent.tasks.agent_avatar.generate_agent_avatar_task.delay") as mocked_delay:
            scheduled = maybe_schedule_agent_avatar(agent)

        self.assertTrue(scheduled)
        mocked_delay.assert_called_once_with(
            str(agent.id),
            compute_charter_hash(agent.charter),
            None,
        )

    @patch("api.tasks.avatar_backfill.is_avatar_image_generation_configured", return_value=True)
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

    @patch("api.tasks.avatar_backfill.is_avatar_image_generation_configured", return_value=True)
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
