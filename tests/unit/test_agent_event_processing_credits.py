from decimal import Decimal

from django.test import TestCase, tag, override_settings
from django.utils import timezone

from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    TaskCredit,
)
from django.contrib.auth import get_user_model

from unittest.mock import MagicMock, patch

import uuid
from util.analytics import AnalyticsEvent, AnalyticsSource
from util.constants.task_constants import TASKS_UNLIMITED
from api.agent.core.event_processing import (
    _add_budget_awareness_sections,
    _compute_burn_rate,
    _ensure_credit_for_tool,
)


class _DummySpan:
    def add_event(self, *_args, **_kwargs):
        return None

    def set_attribute(self, *_args, **_kwargs):
        return None


@tag("batch_event_processing")
class PersistentAgentCreditGateTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._short_desc_patcher = patch(
            "api.agent.tasks.short_description.generate_agent_short_description_task.delay",
            return_value=None,
        )
        cls._short_desc_patcher.start()
        cls._mini_desc_patcher = patch(
            "api.agent.tasks.mini_description.generate_agent_mini_description_task.delay",
            return_value=None,
        )
        cls._mini_desc_patcher.start()
        cls._tags_patcher = patch(
            "api.agent.tasks.agent_tags.generate_agent_tags_task.delay",
            return_value=None,
        )
        cls._tags_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls._short_desc_patcher.stop()
        cls._mini_desc_patcher.stop()
        cls._tags_patcher.stop()
        super().tearDownClass()

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

        with patch("config.settings.GOBII_PROPRIETARY_MODE", True), \
             patch("api.agent.core.event_processing._run_agent_loop") as loop_mock:
            # Return empty dict for token usage
            loop_mock.return_value = {}
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

        with patch("config.settings.GOBII_PROPRIETARY_MODE", False), \
             patch("api.agent.core.event_processing._run_agent_loop") as loop_mock:
            # Return empty dict for token usage
            loop_mock.return_value = {}
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

    def test_process_agent_events_respects_daily_limit(self):
        """Processing should exit early when the agent hit its daily limit."""
        from api.agent.core.event_processing import _process_agent_events_locked

        self.agent.daily_credit_limit = 1
        self.agent.save(update_fields=["daily_credit_limit"])
        with patch('tasks.services.TaskCreditService.check_and_consume_credit_for_owner', return_value={'success': True, 'credit': None}):
            PersistentAgentStep.objects.create(
                agent=self.agent,
                description="Previously consumed",
                credits_cost=Decimal("2"),
            )

        fake_state = {
            "date": timezone.localdate(),
            "limit": Decimal("1"),
            "soft_target": Decimal("1"),
            "used": Decimal("2"),
            "remaining": Decimal("0"),
            "soft_target_remaining": Decimal("0"),
            "hard_limit": Decimal("2"),
            "hard_limit_remaining": Decimal("0"),
            "next_reset": timezone.now(),
        }

        with override_settings(GOBII_PROPRIETARY_MODE=True), \
             patch("config.settings.GOBII_PROPRIETARY_MODE", True), \
             patch("api.agent.core.event_processing._get_agent_daily_credit_state", return_value=fake_state), \
             patch("api.agent.core.event_processing._run_agent_loop") as loop_mock:
            _process_agent_events_locked(self.agent.id, _DummySpan())
            loop_mock.assert_not_called()

        self.assertTrue(
            PersistentAgentSystemStep.objects.filter(
                step__agent=self.agent,
                code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
                notes="daily_credit_limit_exhausted",
            ).exists()
        )

    def test_proprietary_mode_unlimited_allows_processing(self):
        # In proprietary mode, if availability is unlimited (-1), we should proceed
        with patch("config.settings.GOBII_PROPRIETARY_MODE", True), \
             patch("api.agent.core.event_processing.TaskCreditService.get_user_task_credits_available", return_value=TASKS_UNLIMITED), \
             patch("api.agent.core.event_processing._run_agent_loop") as loop_mock:
            # Return empty dict for token usage
            loop_mock.return_value = {}
            from api.agent.core.event_processing import _process_agent_events_locked

            _process_agent_events_locked(self.agent.id, _DummySpan())

            loop_mock.assert_called()
        
        # Ensure no credit_insufficient note was written
        self.assertFalse(
            PersistentAgentSystemStep.objects.filter(
                step__agent=self.agent,
                code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
                notes__icontains="credit_insufficient",
            ).exists()
        )

    def test_process_events_step_without_usage_has_no_completion(self):
        self._grant_credits(credits=1, used=0)
        zero_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "model": None,
            "provider": None,
        }

        with patch("config.settings.GOBII_PROPRIETARY_MODE", True), \
             patch("api.agent.core.event_processing._run_agent_loop", return_value=zero_usage):
            from api.agent.core.event_processing import _process_agent_events_locked

            _process_agent_events_locked(self.agent.id, _DummySpan())

        step = PersistentAgentStep.objects.get(agent=self.agent, description="Process events")
        self.assertIsNone(step.completion)


@tag("batch_event_processing")
class PersistentAgentToolCreditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username=f"tool-user-{uuid.uuid4()}",
            email=f"tool-user-{uuid.uuid4()}@example.com",
            password="pass1234",
        )

        cls.browser_agent = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Tool BA",
        )

        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Tool Agent",
            charter="Handle tool credits",
            browser_use_agent=cls.browser_agent,
        )

    def tearDown(self):
        PersistentAgentStep.objects.filter(agent=self.agent).delete()
        PersistentAgentSystemStep.objects.filter(step__agent=self.agent).delete()

    @patch("api.agent.core.event_processing.settings.GOBII_PROPRIETARY_MODE", True)
    @patch("api.agent.core.event_processing.TaskCreditService.check_and_consume_credit")
    @patch("api.agent.core.event_processing.TaskCreditService.get_user_task_credits_available")
    @patch("api.agent.core.event_processing.get_tool_credit_cost", return_value=Decimal("0.8"))
    def test_mid_loop_insufficient_when_cost_exceeds_available(
        self,
        mock_cost,
        mock_available,
        mock_consume,
    ):
        mock_available.return_value = Decimal("0.4")
        span = MagicMock()

        result = _ensure_credit_for_tool(self.agent, "sqlite_query", span=span)

        self.assertFalse(result)
        mock_consume.assert_not_called()

        step = PersistentAgentStep.objects.get(agent=self.agent)
        self.assertIn("insufficient credits", step.description)
        self.assertTrue(
            PersistentAgentSystemStep.objects.filter(
                step=step,
                notes="credit_insufficient_mid_loop",
            ).exists()
        )

        span.add_event.assert_any_call("Tool skipped - insufficient credits mid-loop")
        span.set_attribute.assert_any_call("credit_check.tool_cost", 0.8)

    @patch("api.agent.core.event_processing.settings.GOBII_PROPRIETARY_MODE", True)
    @patch("api.agent.core.event_processing.TaskCreditService.check_and_consume_credit")
    @patch("api.agent.core.event_processing.TaskCreditService.get_user_task_credits_available", return_value=Decimal("1.2"))
    @patch("api.agent.core.event_processing.get_tool_credit_cost", return_value=Decimal("0.8"))
    def test_mid_loop_consumption_exception_records_error(
        self,
        mock_cost,
        _mock_available,
        mock_consume,
    ):
        mock_consume.side_effect = Exception("db down")
        span = MagicMock()

        result = _ensure_credit_for_tool(self.agent, "sqlite_query", span=span)

        self.assertFalse(result)
        step = PersistentAgentStep.objects.get(agent=self.agent)
        self.assertIn("insufficient credits", step.description)
        self.assertTrue(
            PersistentAgentSystemStep.objects.filter(
                step=step,
                notes="credit_consumption_failure_mid_loop",
            ).exists()
        )

        span.add_event.assert_any_call("Credit consumption raised exception", {"error": "db down"})
        span.add_event.assert_any_call("Tool skipped - insufficient credits during processing")
        span.set_attribute.assert_any_call("credit_check.error", "db down")

    @patch("api.agent.core.event_processing.settings.GOBII_PROPRIETARY_MODE", True)
    @patch(
        "api.agent.core.event_processing.TaskCreditService.check_and_consume_credit",
        return_value={"success": True, "credit": None},
    )
    @patch(
        "api.agent.core.event_processing.TaskCreditService.get_user_task_credits_available",
        return_value=Decimal("10"),
    )
    @patch("api.agent.core.event_processing.get_tool_credit_cost", return_value=Decimal("1"))
    def test_soft_target_exceedance_allows_until_hard_limit(
        self,
        _mock_cost,
        _mock_available,
        _mock_consume,
    ):
        self.agent.daily_credit_limit = 5
        self.agent.save(update_fields=["daily_credit_limit"])
        PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Soft target exceeded",
            credits_cost=Decimal("6"),
        )

        span = MagicMock()
        result = _ensure_credit_for_tool(self.agent, "sqlite_query", span=span)

        self.assertEqual(result, Decimal("1"))
        self.assertFalse(
            self.agent.steps.filter(description__icontains="Skipped tool").exists(),
            "Soft target exhaustion should not emit a skip step until the hard limit is reached.",
        )

    @patch("api.agent.core.event_processing.Analytics.track_event")
    @patch("api.agent.core.event_processing.settings.GOBII_PROPRIETARY_MODE", True)
    @patch(
        "api.agent.core.event_processing.TaskCreditService.check_and_consume_credit",
        return_value={"success": True, "credit": None},
    )
    @patch(
        "api.agent.core.event_processing.TaskCreditService.get_user_task_credits_available",
        return_value=Decimal("10"),
    )
    @patch("api.agent.core.event_processing.get_tool_credit_cost", return_value=Decimal("1"))
    def test_soft_target_exceedance_emits_analytics(
        self,
        _mock_cost,
        _mock_available,
        _mock_consume,
        mock_track_event,
    ):
        self.agent.daily_credit_limit = 5
        self.agent.save(update_fields=["daily_credit_limit"])
        PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Soft target threshold reached",
            credits_cost=Decimal("5"),
        )

        result = _ensure_credit_for_tool(self.agent, "sqlite_query")

        self.assertEqual(result, Decimal("1"))
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["event"], AnalyticsEvent.PERSISTENT_AGENT_SOFT_LIMIT_EXCEEDED)
        self.assertEqual(kwargs["source"], AnalyticsSource.AGENT)
        self.assertEqual(kwargs["properties"].get("agent_id"), str(self.agent.id))

    @patch("api.agent.core.event_processing.settings.GOBII_PROPRIETARY_MODE", True)
    @patch("api.agent.core.event_processing.TaskCreditService.check_and_consume_credit")
    @patch("api.agent.core.event_processing.TaskCreditService.get_user_task_credits_available", return_value=TASKS_UNLIMITED)
    @patch("api.agent.core.event_processing.get_tool_credit_cost", return_value=Decimal("0.8"))
    def test_unlimited_skips_fractional_gate(
        self,
        mock_cost,
        _mock_available,
        mock_consume,
    ):
        mock_consume.return_value = {"success": True, "credit": object()}
        span = MagicMock()

        result = _ensure_credit_for_tool(self.agent, "sqlite_query", span=span)

        self.assertEqual(result, Decimal("0.8"))
        mock_consume.assert_called_once()
        span.set_attribute.assert_any_call("credit_check.consumed_in_loop", True)

    @patch("api.agent.core.event_processing.settings.GOBII_PROPRIETARY_MODE", True)
    @patch("api.agent.core.event_processing.TaskCreditService.check_and_consume_credit")
    @patch("api.agent.core.event_processing.TaskCreditService.get_user_task_credits_available", return_value=Decimal("5"))
    @patch("api.agent.core.event_processing.get_tool_credit_cost", return_value=Decimal("0.5"))
    def test_mid_loop_daily_limit_blocks_tool(
        self,
        mock_cost,
        _mock_available,
        mock_consume,
    ):
        span = MagicMock()
        self.agent.daily_credit_limit = 1
        self.agent.save(update_fields=["daily_credit_limit"])
        with patch('tasks.services.TaskCreditService.check_and_consume_credit_for_owner', return_value={'success': True, 'credit': None}):
            PersistentAgentStep.objects.create(
                agent=self.agent,
                description="Partial usage",
                credits_cost=Decimal("2.0"),
            )

        result = _ensure_credit_for_tool(self.agent, "sqlite_query", span=span)

        self.assertFalse(result)
        mock_consume.assert_not_called()
        step = PersistentAgentStep.objects.filter(agent=self.agent).order_by('-created_at').first()
        self.assertIsNotNone(step)
        self.assertIn("daily credit limit", step.description.lower())
        self.assertTrue(
            PersistentAgentSystemStep.objects.filter(
                step=step,
                notes="daily_credit_limit_mid_loop",
            ).exists()
        )
        span.add_event.assert_any_call("Tool skipped - daily credit limit reached")

    def test_compute_burn_rate_no_data_returns_zero(self):
        metrics = _compute_burn_rate(self.agent, window_minutes=60)
        self.assertEqual(metrics["burn_rate_per_hour"], Decimal("0"))
        self.assertEqual(metrics["window_total"], Decimal("0"))

    def test_compute_burn_rate_counts_recent_usage(self):
        with patch('tasks.services.TaskCreditService.check_and_consume_credit_for_owner', return_value={'success': True, 'credit': None}):
            step = PersistentAgentStep.objects.create(
                agent=self.agent,
                description="Recent usage",
                credits_cost=Decimal("3.0"),
            )
        PersistentAgentStep.objects.filter(pk=step.pk).update(
            created_at=timezone.now() - timezone.timedelta(minutes=10)
        )
        metrics = _compute_burn_rate(self.agent, window_minutes=60)
        self.assertEqual(metrics["burn_rate_per_hour"], Decimal("3"))
        self.assertEqual(metrics["window_total"], Decimal("3"))

    @patch(
        "api.agent.core.event_processing.get_tool_cost_overview",
        return_value=(Decimal("1"), {"send_email": Decimal("1.2"), "run_sql": Decimal("2.5")}),
    )
    def test_budget_sections_include_soft_target_and_burn_warning(self, _mock_costs):
        critical_group = MagicMock()
        budget_group = MagicMock()
        critical_group.group.return_value = budget_group
        next_reset = timezone.now() + timezone.timedelta(hours=4)
        state = {
            "limit": Decimal("10"),
            "soft_target": Decimal("5"),
            "used": Decimal("4"),
            "remaining": Decimal("6"),
            "soft_target_remaining": Decimal("1"),
            "next_reset": next_reset,
            "burn_rate_per_hour": Decimal("5"),
            "burn_rate_window_minutes": 60,
            "burn_rate_threshold_per_hour": Decimal("3"),
        }
        result = _add_budget_awareness_sections(
            critical_group,
            current_iteration=2,
            max_iterations=4,
            daily_credit_state=state,
        )
        self.assertTrue(result)
        names = [call.args[0] for call in budget_group.section_text.call_args_list]
        self.assertIn("soft_target_progress", names)
        self.assertIn("burn_rate_warning", names)
        self.assertIn("tool_cost_awareness", names)
        soft_call = next(call for call in budget_group.section_text.call_args_list if call.args[0] == "soft_target_progress")
        self.assertIn("Soft target progress", soft_call.args[1])
        tool_call = next(call for call in budget_group.section_text.call_args_list if call.args[0] == "tool_cost_awareness")
        self.assertIn("send_email=1.2", tool_call.args[1])

    def test_budget_sections_handle_unlimited_soft_target(self):
        critical_group = MagicMock()
        budget_group = MagicMock()
        critical_group.group.return_value = budget_group
        state = {
            "limit": None,
            "soft_target": None,
            "used": Decimal("3"),
            "remaining": None,
            "soft_target_remaining": None,
            "next_reset": timezone.now(),
        }
        result = _add_budget_awareness_sections(
            critical_group,
            current_iteration=1,
            max_iterations=0,
            daily_credit_state=state,
        )
        self.assertTrue(result)
        names = [call.args[0] for call in budget_group.section_text.call_args_list]
        self.assertNotIn("soft_target_progress", names)
