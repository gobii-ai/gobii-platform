"""Ensure pending work keeps the budget cycle open on sleep."""
from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core.budget import AgentBudgetManager, BudgetContext
from api.agent.core.event_processing import _attempt_cycle_close_for_sleep
from api.agent.core.processing_flags import enqueue_pending_agent, pending_set_key
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
