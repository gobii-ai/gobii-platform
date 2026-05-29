from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings, tag

from api.agent.core.event_processing import _ensure_credit_for_tool
from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    EvalRun,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentStep,
    TaskCredit,
)


@tag("batch_event_processing_credits")
@override_settings(GOBII_PROPRIETARY_MODE=True, GOBII_ENABLE_COMMUNITY_UNLIMITED=False)
class EvalCreditPolicyTests(TestCase):
    def _user(self, username: str):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.test",
            password="secret",
        )
        TaskCredit.objects.filter(user=user).delete()
        return user

    def _org(self, user, slug: str):
        organization = Organization.objects.create(
            name=slug.replace("-", " ").title(),
            slug=slug,
            created_by=user,
        )
        organization.billing.purchased_seats = 1
        organization.billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=organization,
            user=user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        return organization

    def _agent(self, *, username: str, slug: str, execution_environment: str = "local"):
        user = self._user(username)
        organization = self._org(user, slug)
        browser_agent = BrowserUseAgent.objects.create(
            user=user,
            name=f"{username}-browser",
        )
        agent = PersistentAgent.objects.create(
            user=user,
            organization=organization,
            browser_use_agent=browser_agent,
            name=f"{username}-agent",
            charter="Test agent.",
            execution_environment=execution_environment,
        )
        TaskCredit.objects.filter(user=user).delete()
        TaskCredit.objects.filter(organization=organization).delete()
        return user, organization, browser_agent, agent

    def test_eval_step_with_explicit_credit_cost_does_not_consume_task_credit(self):
        _user, organization, _browser_agent, agent = self._agent(
            username="eval-step",
            slug="eval-step-org",
            execution_environment="eval",
        )

        step = PersistentAgentStep.objects.create(
            agent=agent,
            description="Seed eval burn.",
            credits_cost=Decimal("45.000"),
        )

        self.assertIsNone(step.task_credit_id)
        self.assertEqual(
            TaskCredit.objects.filter(organization=organization).count(),
            0,
        )

    def test_non_eval_step_with_explicit_credit_cost_still_requires_credit(self):
        _user, _organization, _browser_agent, agent = self._agent(
            username="normal-step",
            slug="normal-step-org",
        )

        with self.assertRaises(ValidationError):
            PersistentAgentStep.objects.create(
                agent=agent,
                description="Billable work.",
                credits_cost=Decimal("1.000"),
            )

    def test_eval_tool_credit_check_bypasses_consumption(self):
        _user, _organization, _browser_agent, agent = self._agent(
            username="eval-tool",
            slug="eval-tool-org",
            execution_environment="eval",
        )

        with patch(
            "api.agent.core.event_processing.TaskCreditService.calculate_available_tasks_for_owner"
        ) as calculate_available, patch(
            "api.agent.core.event_processing.TaskCreditService.check_and_consume_credit_for_owner"
        ) as consume_credit:
            result = _ensure_credit_for_tool(
                agent,
                "http_request",
                eval_run_id=str(uuid4()),
            )

        self.assertEqual(result, {"cost": None, "credit": None})
        calculate_available.assert_not_called()
        consume_credit.assert_not_called()

    def test_non_eval_tool_credit_check_still_blocks_without_credit(self):
        _user, _organization, _browser_agent, agent = self._agent(
            username="normal-tool",
            slug="normal-tool-org",
        )

        with patch(
            "api.agent.core.event_processing.get_tool_credit_cost",
            return_value=Decimal("1.000"),
        ), patch(
            "api.agent.core.event_processing.TaskCreditService.calculate_available_tasks_for_owner",
            return_value=Decimal("0"),
        ) as calculate_available, patch(
            "api.agent.core.event_processing.TaskCreditService.check_and_consume_credit_for_owner"
        ) as consume_credit:
            result = _ensure_credit_for_tool(agent, "http_request")

        self.assertFalse(result)
        calculate_available.assert_called_once()
        consume_credit.assert_not_called()

    def test_eval_browser_task_does_not_consume_task_credit(self):
        user, organization, browser_agent, agent = self._agent(
            username="eval-browser",
            slug="eval-browser-org",
            execution_environment="eval",
        )
        run = EvalRun.objects.create(
            scenario_slug="eval_credit_policy",
            scenario_version="1.0.0",
            agent=agent,
            initiated_by=user,
        )

        task = BrowserUseAgentTask.objects.create(
            agent=browser_agent,
            user=user,
            organization=organization,
            eval_run=run,
            prompt="Eval browser task.",
            credits_cost=Decimal("5.000"),
        )

        self.assertIsNone(task.task_credit_id)
        self.assertEqual(
            TaskCredit.objects.filter(organization=organization).count(),
            0,
        )

    def test_non_eval_browser_task_still_requires_credit(self):
        user, organization, browser_agent, _agent = self._agent(
            username="normal-browser",
            slug="normal-browser-org",
        )

        with self.assertRaises(ValidationError):
            BrowserUseAgentTask.objects.create(
                agent=browser_agent,
                user=user,
                organization=organization,
                prompt="Normal browser task.",
                credits_cost=Decimal("5.000"),
            )
