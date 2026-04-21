from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.agent.core.event_processing import (
    _PreparedToolExecution,
    _ToolExecutionOutcome,
    _finalize_tool_batch,
    _refund_tool_credit_on_error,
    _should_refund_on_error,
)
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentToolCall,
    TaskCredit,
    TaskCreditConfig,
)
from tasks.services import TaskCreditService
from util.tool_costs import clear_tool_credit_cost_cache


User = get_user_model()


def _noop_attach(_kwargs):
    return None


def _noop_archive(_step):
    return None


@tag("batch_tool_credit_refund")
class TaskCreditServiceRefundCreditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="refund-svc@example.com",
            email="refund-svc@example.com",
            password="password123",
        )
        # Signup auto-grants a TaskCredit; reuse it and set it to a known state.
        self.credit = (
            TaskCredit.objects
            .filter(user=self.user, voided=False, expiration_date__gte=timezone.now())
            .order_by("expiration_date")
            .first()
        )
        self.assertIsNotNone(self.credit)
        self.credit.credits = Decimal("10.000")
        self.credit.credits_used = Decimal("5.000")
        self.credit.save(update_fields=["credits", "credits_used"])

    def test_refund_decrements_credits_used(self):
        result = TaskCreditService.refund_credit(self.credit, Decimal("0.4"))
        self.assertTrue(result)
        self.credit.refresh_from_db()
        self.assertEqual(self.credit.credits_used, Decimal("4.600"))

    def test_refund_clamps_at_zero(self):
        self.credit.credits_used = Decimal("0.1")
        self.credit.save(update_fields=["credits_used"])

        result = TaskCreditService.refund_credit(self.credit, Decimal("0.4"))
        self.assertTrue(result)
        self.credit.refresh_from_db()
        self.assertEqual(self.credit.credits_used, Decimal("0.000"))

    def test_refund_returns_false_for_none_credit(self):
        self.assertFalse(TaskCreditService.refund_credit(None, Decimal("0.1")))
        self.credit.refresh_from_db()
        self.assertEqual(self.credit.credits_used, Decimal("5.000"))

    def test_refund_returns_false_for_none_amount(self):
        self.assertFalse(TaskCreditService.refund_credit(self.credit, None))
        self.credit.refresh_from_db()
        self.assertEqual(self.credit.credits_used, Decimal("5.000"))

    def test_refund_returns_false_for_zero_or_negative(self):
        self.assertFalse(TaskCreditService.refund_credit(self.credit, Decimal("0")))
        self.assertFalse(TaskCreditService.refund_credit(self.credit, Decimal("-1")))
        self.credit.refresh_from_db()
        self.assertEqual(self.credit.credits_used, Decimal("5.000"))

    def test_refund_returns_false_for_invalid_amount(self):
        self.assertFalse(TaskCreditService.refund_credit(self.credit, "not-a-number"))
        self.credit.refresh_from_db()
        self.assertEqual(self.credit.credits_used, Decimal("5.000"))


@tag("batch_tool_credit_refund")
class ShouldRefundOnErrorTests(TestCase):
    @override_settings(TOOL_CREDIT_REFUND_ON_ERROR=["create_video"])
    def test_matches_case_insensitively(self):
        self.assertTrue(_should_refund_on_error("create_video"))
        self.assertTrue(_should_refund_on_error("CREATE_VIDEO"))
        self.assertTrue(_should_refund_on_error("Create_Video"))

    @override_settings(TOOL_CREDIT_REFUND_ON_ERROR=["create_video"])
    def test_non_allowlisted_tool_returns_false(self):
        self.assertFalse(_should_refund_on_error("search_web"))
        self.assertFalse(_should_refund_on_error(""))
        self.assertFalse(_should_refund_on_error(None))

    @override_settings(TOOL_CREDIT_REFUND_ON_ERROR=[])
    def test_empty_allowlist_returns_false(self):
        self.assertFalse(_should_refund_on_error("create_video"))


@tag("batch_tool_credit_refund")
class RefundToolCreditOnErrorHelperTests(TestCase):
    def setUp(self):
        clear_tool_credit_cost_cache()
        TaskCreditConfig.objects.update_or_create(
            singleton_id=1,
            defaults={"default_task_cost": Decimal("0.40")},
        )
        self.user = User.objects.create_user(
            username="refund-helper@example.com",
            email="refund-helper@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Agent",
            charter="do things",
            browser_use_agent=self.browser_agent,
        )
        self.credit = (
            TaskCredit.objects
            .filter(user=self.user, voided=False, expiration_date__gte=timezone.now())
            .order_by("expiration_date")
            .first()
        )
        self.assertIsNotNone(self.credit)
        self.credit.credits = Decimal("100.000")
        self.credit.credits_used = Decimal("2.000")
        self.credit.save(update_fields=["credits", "credits_used"])
        self.step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Pre-charged step",
            credits_cost=Decimal("2.000"),
            task_credit=self.credit,
        )

    def test_refunds_and_clears_step_fields(self):
        refunded = _refund_tool_credit_on_error(
            agent=self.agent,
            tool_name="create_video",
            pending_step=self.step,
            consumed_credit=self.credit,
            credits_consumed=Decimal("2.000"),
        )
        self.assertTrue(refunded)

        self.credit.refresh_from_db()
        self.step.refresh_from_db()
        self.assertEqual(self.credit.credits_used, Decimal("0.000"))
        self.assertIsNone(self.step.credits_cost)
        self.assertIsNone(self.step.task_credit_id)

    def test_no_op_when_consumed_credit_is_none(self):
        refunded = _refund_tool_credit_on_error(
            agent=self.agent,
            tool_name="create_video",
            pending_step=self.step,
            consumed_credit=None,
            credits_consumed=Decimal("2.000"),
        )
        self.assertFalse(refunded)

        self.credit.refresh_from_db()
        self.step.refresh_from_db()
        self.assertEqual(self.credit.credits_used, Decimal("2.000"))
        self.assertEqual(self.step.credits_cost, Decimal("2.000"))
        self.assertEqual(self.step.task_credit_id, self.credit.id)

    def test_no_op_when_credits_consumed_is_none(self):
        refunded = _refund_tool_credit_on_error(
            agent=self.agent,
            tool_name="create_video",
            pending_step=self.step,
            consumed_credit=self.credit,
            credits_consumed=None,
        )
        self.assertFalse(refunded)

        self.credit.refresh_from_db()
        self.assertEqual(self.credit.credits_used, Decimal("2.000"))

    def test_swallows_refund_exceptions(self):
        with patch(
            "api.agent.core.event_processing.TaskCreditService.refund_credit",
            side_effect=RuntimeError("boom"),
        ):
            refunded = _refund_tool_credit_on_error(
                agent=self.agent,
                tool_name="create_video",
                pending_step=self.step,
                consumed_credit=self.credit,
                credits_consumed=Decimal("2.000"),
            )
        self.assertFalse(refunded)
        # Credit and step remain untouched because refund_credit itself failed.
        self.credit.refresh_from_db()
        self.step.refresh_from_db()
        self.assertEqual(self.credit.credits_used, Decimal("2.000"))
        self.assertEqual(self.step.credits_cost, Decimal("2.000"))


@tag("batch_tool_credit_refund")
class FinalizeToolBatchRefundTests(TestCase):
    def setUp(self):
        clear_tool_credit_cost_cache()
        TaskCreditConfig.objects.update_or_create(
            singleton_id=1,
            defaults={"default_task_cost": Decimal("0.40")},
        )
        self.user = User.objects.create_user(
            username="refund-finalize@example.com",
            email="refund-finalize@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Agent",
            charter="do things",
            browser_use_agent=self.browser_agent,
        )
        self.credit = (
            TaskCredit.objects
            .filter(user=self.user, voided=False, expiration_date__gte=timezone.now())
            .order_by("expiration_date")
            .first()
        )
        self.assertIsNotNone(self.credit)
        self.credit.credits = Decimal("100.000")
        self.credit.credits_used = Decimal("2.000")
        self.credit.save(update_fields=["credits", "credits_used"])

    def _build_prepared(self, tool_name, credits_consumed=Decimal("2.000"), consumed_credit=None):
        pending_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="",
            credits_cost=credits_consumed,
            task_credit=consumed_credit if consumed_credit is not None else self.credit,
        )
        PersistentAgentToolCall.objects.create(
            step=pending_step,
            tool_name=tool_name,
            tool_params={},
            result="",
            execution_duration_ms=None,
            status="pending",
        )
        return _PreparedToolExecution(
            idx=0,
            tool_name=tool_name,
            tool_params={},
            exec_params={},
            pending_step=pending_step,
            credits_consumed=credits_consumed,
            consumed_credit=consumed_credit if consumed_credit is not None else self.credit,
            call_id="call-1",
            explicit_continue=None,
            inferred_continue=False,
            parallel_safe=False,
            parallel_ineligible_reason=None,
        )

    @override_settings(TOOL_CREDIT_REFUND_ON_ERROR=["create_video"])
    def test_error_from_allowlisted_tool_refunds_credit(self):
        prepared = self._build_prepared("create_video")
        outcome = _ToolExecutionOutcome(
            prepared=prepared,
            result={"status": "error", "message": "upstream down"},
            duration_ms=5,
            updated_tools=None,
            variable_map={},
        )

        _finalize_tool_batch(
            self.agent,
            [outcome],
            attach_completion=_noop_attach,
            attach_prompt_archive=_noop_archive,
        )

        self.credit.refresh_from_db()
        prepared.pending_step.refresh_from_db()
        tool_call = PersistentAgentToolCall.objects.get(step=prepared.pending_step)
        self.assertEqual(self.credit.credits_used, Decimal("0.000"))
        self.assertIsNone(prepared.pending_step.credits_cost)
        self.assertIsNone(prepared.pending_step.task_credit_id)
        self.assertEqual(tool_call.status, "error")

    @override_settings(TOOL_CREDIT_REFUND_ON_ERROR=["create_video"])
    def test_success_from_allowlisted_tool_does_not_refund(self):
        prepared = self._build_prepared("create_video")
        outcome = _ToolExecutionOutcome(
            prepared=prepared,
            result={"status": "ok", "video_url": "https://..."},
            duration_ms=5,
            updated_tools=None,
            variable_map={},
        )

        _finalize_tool_batch(
            self.agent,
            [outcome],
            attach_completion=_noop_attach,
            attach_prompt_archive=_noop_archive,
        )

        self.credit.refresh_from_db()
        prepared.pending_step.refresh_from_db()
        self.assertEqual(self.credit.credits_used, Decimal("2.000"))
        self.assertEqual(prepared.pending_step.credits_cost, Decimal("2.000"))
        self.assertEqual(prepared.pending_step.task_credit_id, self.credit.id)

    @override_settings(TOOL_CREDIT_REFUND_ON_ERROR=["create_video"])
    def test_error_from_non_allowlisted_tool_keeps_charge(self):
        prepared = self._build_prepared("search_web")
        outcome = _ToolExecutionOutcome(
            prepared=prepared,
            result={"status": "error", "message": "no results"},
            duration_ms=5,
            updated_tools=None,
            variable_map={},
        )

        _finalize_tool_batch(
            self.agent,
            [outcome],
            attach_completion=_noop_attach,
            attach_prompt_archive=_noop_archive,
        )

        self.credit.refresh_from_db()
        prepared.pending_step.refresh_from_db()
        self.assertEqual(self.credit.credits_used, Decimal("2.000"))
        self.assertEqual(prepared.pending_step.credits_cost, Decimal("2.000"))
        self.assertEqual(prepared.pending_step.task_credit_id, self.credit.id)

