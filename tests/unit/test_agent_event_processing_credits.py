from django.test import TestCase, tag
from django.utils import timezone

from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentSystemStep,
    TaskCredit,
)
from django.contrib.auth import get_user_model

from unittest.mock import patch

import uuid


class _DummySpan:
    def add_event(self, *_args, **_kwargs):
        return None

    def set_attribute(self, *_args, **_kwargs):
        return None


@tag("batch_event_processing")
class PersistentAgentCreditGateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username=f"user-{uuid.uuid4()}",
            email=f"user-{uuid.uuid4()}@example.com",
            password="pass1234",
        )

        cls.browser_agent = BrowserUseAgent.objects.create(
            user=cls.user,
            name="BA for PA",
        )

        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Test Persistent Agent",
            charter="Do useful things",
            browser_use_agent=cls.browser_agent,
        )

    def _grant_credits(self, credits: int, used: int):
        now = timezone.now()
        TaskCredit.objects.create(
            user=self.user,
            credits=credits,
            credits_used=used,
            granted_date=now,
            expiration_date=now + timezone.timedelta(days=30),
            grant_type="Compensation",
        )

    def test_proprietary_mode_out_of_credits_exits_early(self):
        # Force the credit check to report 0 available
        with patch("config.settings.GOBII_PROPRIETARY_MODE", True), patch(
            "api.agent.core.event_processing.TaskCreditService.get_user_task_credits_available",
            return_value=0,
        ):
            # Patch the heavy loop to ensure it would raise if called
            with patch("api.agent.core.event_processing._run_agent_loop") as loop_mock:
                from api.agent.core.event_processing import _process_agent_events_locked

                _process_agent_events_locked(self.agent.id, _DummySpan())

                # Ensure loop never runs due to early exit
                loop_mock.assert_not_called()

        # The early exit creates a SystemStep with PROCESS_EVENTS + credit_insufficient
        sys_steps = PersistentAgentSystemStep.objects.filter(
            step__agent=self.agent,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )
        self.assertTrue(sys_steps.exists(), "Expected a system step to be created on early exit")

        notes = list(sys_steps.values_list("notes", flat=True))
        self.assertIn("credit_insufficient", notes)

        # Ensure that no "Process events" description (from normal path) was created
        self.assertFalse(
            self.agent.steps.filter(description="Process events").exists(),
            "Normal event-window step should not be created on early exit",
        )

    def test_proprietary_mode_with_credits_proceeds(self):
        # Give at least one available credit
        self._grant_credits(credits=1, used=0)

        with patch("config.settings.GOBII_PROPRIETARY_MODE", True), patch("api.agent.core.event_processing._run_agent_loop") as loop_mock:
            from api.agent.core.event_processing import _process_agent_events_locked

            _process_agent_events_locked(self.agent.id, _DummySpan())

            # Should proceed into normal path
            loop_mock.assert_called()

        # Should have created the normal PROCESS_EVENTS step (description = "Process events")
        self.assertTrue(
            self.agent.steps.filter(description="Process events").exists(),
            "Expected normal event processing step to be created",
        )

        # And should NOT include the credit_insufficient system note
        self.assertFalse(
            PersistentAgentSystemStep.objects.filter(
                step__agent=self.agent,
                code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
                notes__icontains="credit_insufficient",
            ).exists()
        )

    def test_non_proprietary_mode_skips_gate(self):
        # Even with no available credits, in non-proprietary mode we proceed
        self._grant_credits(credits=100, used=100)

        with patch("config.settings.GOBII_PROPRIETARY_MODE", False), patch("api.agent.core.event_processing._run_agent_loop") as loop_mock:
            from api.agent.core.event_processing import _process_agent_events_locked

            _process_agent_events_locked(self.agent.id, _DummySpan())

            loop_mock.assert_called()

        # No credit_insufficient note expected
        self.assertFalse(
            PersistentAgentSystemStep.objects.filter(
                step__agent=self.agent,
                code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
                notes__icontains="credit_insufficient",
            ).exists()
        )
