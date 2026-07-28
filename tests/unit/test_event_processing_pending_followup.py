"""Ensure queued follow-up work survives active processing."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core import event_processing as ep
from api.agent.core.budget import AgentBudgetManager, BudgetContext
from api.agent.core.event_processing import _attempt_cycle_close_for_sleep
from api.agent.core.llm_utils import StreamIdleTimeout
from api.agent.core.processing_flags import (
    bump_human_inbound_generation,
    claim_pending_agent,
    enqueue_pending_agent,
    get_processing_heartbeat,
    get_processing_locked_agent_ids,
    mark_human_inbound_generation_consumed,
    pending_set_key,
    remove_pending_agent,
    set_processing_queued_flag,
)
from api.agent.tasks.process_events import AGENT_INTERACTIVE_PROCESSING_QUEUE
from api.models import BrowserUseAgent, PersistentAgent
from config.redis_client import get_redis_client


@tag("batch_event_processing")
class PendingFollowUpClosureTests(TestCase):
    """Prove pending work blocks cycle closure on sleep."""

    @classmethod
    def setUpTestData(cls) -> None:
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="pending-followup-user",
            email="pending-followup@example.com",
        )

        cls.browser_agent = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Pending Follow-Up Browser Agent",
        )

        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Pending Follow-Up Agent",
            charter="test",
            browser_use_agent=cls.browser_agent,
        )

    def setUp(self) -> None:
        self.redis = get_redis_client()
        remove_pending_agent(self.agent.id, client=self.redis)
        self.redis.delete(pending_set_key())

    def _build_budget_context(self) -> BudgetContext:
        budget_id, max_steps, max_depth = AgentBudgetManager.find_or_start_cycle(
            agent_id=str(self.agent.id),
        )
        branch_id = AgentBudgetManager.create_branch(
            agent_id=str(self.agent.id),
            budget_id=budget_id,
            depth=0,
        )
        return BudgetContext(
            agent_id=str(self.agent.id),
            budget_id=budget_id,
            branch_id=branch_id,
            depth=0,
            max_steps=max_steps,
            max_depth=max_depth,
        )

    def test_pending_set_keeps_cycle_open(self) -> None:
        budget_ctx = self._build_budget_context()
        enqueue_pending_agent(self.agent.id, client=self.redis, ttl=300)

        _attempt_cycle_close_for_sleep(self.agent, budget_ctx)

        self.assertEqual(
            AgentBudgetManager.get_cycle_status(agent_id=str(self.agent.id)),
            "active",
        )

    def test_processing_queued_flag_keeps_cycle_open(self) -> None:
        budget_ctx = self._build_budget_context()
        set_processing_queued_flag(self.agent.id, ttl=300)

        _attempt_cycle_close_for_sleep(self.agent, budget_ctx)

        self.assertEqual(
            AgentBudgetManager.get_cycle_status(agent_id=str(self.agent.id)),
            "active",
        )

    @patch("api.agent.tasks.process_events.enqueue_claimed_pending_work")
    @patch("api.agent.core.event_processing._process_agent_events_locked")
    @patch("api.agent.core.event_processing.Redlock")
    def test_lock_release_queues_one_generation_aware_followup(
        self,
        mock_redlock,
        mock_process_locked,
        mock_enqueue_followup,
    ) -> None:
        accepted_generation = bump_human_inbound_generation(self.agent.id, client=self.redis)
        lock = MagicMock()
        lock.acquire.return_value = True
        lock.release.return_value = True
        mock_redlock.return_value = lock

        def _record_pending(*_args, **_kwargs):
            newer_generation = bump_human_inbound_generation(self.agent.id, client=self.redis)
            enqueue_pending_agent(
                self.agent.id,
                inbound_generation=newer_generation,
                queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
                client=self.redis,
            )
            return self.agent

        mock_process_locked.side_effect = _record_pending

        followup_queued = ep.process_agent_events(
            self.agent.id,
            inbound_generation=accepted_generation,
            processing_queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
        )

        self.assertTrue(followup_queued)
        mock_enqueue_followup.assert_called_once()
        pending_work = mock_enqueue_followup.call_args.args[0]
        self.assertEqual(pending_work.inbound_generation, accepted_generation + 1)
        self.assertEqual(pending_work.queue, AGENT_INTERACTIVE_PROCESSING_QUEUE)
        self.assertIsNone(claim_pending_agent(self.agent.id, client=self.redis))

    @patch("api.agent.tasks.process_events.enqueue_claimed_pending_work")
    @patch("api.agent.core.event_processing._process_agent_events_locked")
    @patch("api.agent.core.event_processing.Redlock")
    def test_stream_timeout_releases_processing_and_queues_one_pending_followup(
        self,
        mock_redlock,
        mock_process_locked,
        mock_enqueue_followup,
    ) -> None:
        accepted_generation = bump_human_inbound_generation(self.agent.id, client=self.redis)
        lock = MagicMock()
        lock.acquire.return_value = True
        lock.release.return_value = True
        mock_redlock.return_value = lock

        def _timeout_with_pending_work(*_args, **_kwargs):
            newer_generation = bump_human_inbound_generation(self.agent.id, client=self.redis)
            enqueue_pending_agent(
                self.agent.id,
                inbound_generation=newer_generation,
                queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
                client=self.redis,
            )
            raise StreamIdleTimeout(
                "LLM stream produced no additional data",
                model="mock-model",
                llm_provider="mock-provider",
            )

        mock_process_locked.side_effect = _timeout_with_pending_work

        with self.assertRaises(StreamIdleTimeout):
            ep.process_agent_events(
                self.agent.id,
                inbound_generation=accepted_generation,
                processing_queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
            )

        lock.release.assert_called_once()
        mock_enqueue_followup.assert_called_once()
        pending_work = mock_enqueue_followup.call_args.args[0]
        self.assertEqual(pending_work.inbound_generation, accepted_generation + 1)
        self.assertEqual(pending_work.queue, AGENT_INTERACTIVE_PROCESSING_QUEUE)
        self.assertNotIn(
            str(self.agent.id),
            get_processing_locked_agent_ids(client=self.redis),
        )
        self.assertIsNone(get_processing_heartbeat(self.agent.id, client=self.redis))
        self.assertIsNone(claim_pending_agent(self.agent.id, client=self.redis))

    @patch("api.agent.tasks.process_events.enqueue_claimed_pending_work")
    @patch("api.agent.core.event_processing._process_agent_events_locked")
    @patch("api.agent.core.event_processing.Redlock")
    def test_lock_release_discards_generation_consumed_by_active_turn(
        self,
        mock_redlock,
        mock_process_locked,
        mock_enqueue_followup,
    ) -> None:
        generation = bump_human_inbound_generation(self.agent.id, client=self.redis)
        lock = MagicMock()
        lock.acquire.return_value = True
        lock.release.return_value = True
        mock_redlock.return_value = lock

        def _consume_and_record_pending(*_args, **_kwargs):
            enqueue_pending_agent(
                self.agent.id,
                inbound_generation=generation,
                queue=AGENT_INTERACTIVE_PROCESSING_QUEUE,
                client=self.redis,
            )
            mark_human_inbound_generation_consumed(
                self.agent.id,
                generation,
                client=self.redis,
            )
            return self.agent

        mock_process_locked.side_effect = _consume_and_record_pending

        followup_queued = ep.process_agent_events(self.agent.id)

        self.assertFalse(followup_queued)
        mock_enqueue_followup.assert_not_called()
        self.assertIsNone(claim_pending_agent(self.agent.id, client=self.redis))
