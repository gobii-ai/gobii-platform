from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.core.event_processing import _enforce_tool_rate_limit
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    ToolConfig,
    ToolRateLimit,
)
from constants.plans import PlanNamesChoices


@tag("batch_event_processing")
class ToolRateLimitTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="rate-limit@example.com",
            email="rate-limit@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="RL Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="RateLimitedAgent",
            charter="rate limit checks",
            browser_use_agent=self.browser_agent,
        )
        self.tool_config, _ = ToolConfig.objects.get_or_create(plan_name=PlanNamesChoices.FREE)
        self.rate_limit = ToolRateLimit.objects.create(
            plan=self.tool_config,
            tool_name="http_request",
            max_calls_per_hour=2,
        )
        self.now = timezone.now()

    def _make_call(self, minutes_ago: int = 5):
        step = PersistentAgentStep.objects.create(agent=self.agent, description="previous call")
        target_time = self.now - timedelta(minutes=minutes_ago)
        PersistentAgentStep.objects.filter(id=step.id).update(created_at=target_time)
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="http_request",
            tool_params={"url": "https://example.com"},
            result="ok",
        )

    def test_allows_tool_when_under_hourly_limit(self):
        self._make_call(minutes_ago=10)

        allowed = _enforce_tool_rate_limit(self.agent, "http_request")

        self.assertTrue(allowed)

    def test_blocks_tool_at_hourly_limit(self):
        self._make_call(minutes_ago=10)
        self._make_call(minutes_ago=20)

        allowed = _enforce_tool_rate_limit(self.agent, "http_request")

        self.assertFalse(allowed)
        self.assertTrue(
            PersistentAgentStep.objects.filter(
                agent=self.agent,
                description__icontains="hourly limit",
            ).exists()
        )
        system_notes = list(
            PersistentAgentSystemStep.objects.filter(
                step__agent=self.agent,
                notes="tool_hourly_rate_limit",
            )
        )
        self.assertGreaterEqual(len(system_notes), 1)

    def test_unlisted_tool_not_limited(self):
        allowed = _enforce_tool_rate_limit(self.agent, "search_web")

        self.assertTrue(allowed)
        self.assertFalse(
            PersistentAgentSystemStep.objects.filter(
                step__agent=self.agent,
                notes="tool_hourly_rate_limit",
            ).exists()
        )
