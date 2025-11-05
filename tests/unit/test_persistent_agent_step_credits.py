from decimal import Decimal
from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentStep,
    TaskCredit,
    TaskCreditConfig,
    Organization,
)
from util.tool_costs import clear_tool_credit_cost_cache


User = get_user_model()


@tag("batch_pa_step_credits")
class PersistentAgentStepCreditsTests(TestCase):
    def setUp(self):
        clear_tool_credit_cost_cache()
        TaskCreditConfig.objects.update_or_create(
            singleton_id=1,
            defaults={"default_task_cost": Decimal("0.40")},
        )
        self.user = User.objects.create_user(
            username="credits@example.com",
            email="credits@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Agent",
            charter="do things",
            browser_use_agent=self.browser_agent,
        )

    def test_step_creation_consumes_credits_and_sets_fields(self):
        completion = PersistentAgentCompletion.objects.create(agent=self.agent, llm_model="gpt-4")
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Test step",
            completion=completion,
        )
        step.refresh_from_db()

        # Should link to a consumed credit block and set default cost
        self.assertIsNotNone(step.task_credit)
        self.assertIsNotNone(step.credits_cost)
        # The linked credit should have non-zero usage
        credit = step.task_credit
        credit.refresh_from_db()
        self.assertGreater(credit.credits_used, 0)

    def test_fractional_credit_consumption(self):
        config = TaskCreditConfig.objects.get(singleton_id=1)
        config.default_task_cost = Decimal("0.1")
        config.save()
        clear_tool_credit_cost_cache()
        # Find the first valid credit block for the user
        credit = TaskCredit.objects.filter(user=self.user, expiration_date__gte=timezone.now(), voided=False).order_by("expiration_date").first()
        self.assertIsNotNone(credit)
        before_used = credit.credits_used

        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Fractional step",
            completion=PersistentAgentCompletion.objects.create(agent=self.agent, llm_model="gpt-4"),
        )
        step.refresh_from_db()
        credit.refresh_from_db()

        self.assertEqual(step.credits_cost, Decimal("0.1"))
        self.assertEqual(credit.credits_used, before_used + Decimal("0.1"))

    def test_override_credits_cost_on_creation(self):
        # Override the per-step cost explicitly
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Custom cost",
            credits_cost=Decimal("0.25"),
        )
        step.refresh_from_db()
        self.assertEqual(step.credits_cost, Decimal("0.25"))
        self.assertIsNotNone(step.task_credit)

    def test_org_owned_agent_consumes_org_credits(self):
        # Create an organization and grant it credits
        org = Organization.objects.create(
            name="Acme Co",
            slug="acme",
            plan="startup",
            created_by=self.user,
        )
        billing = org.billing
        billing.purchased_seats = 1
        billing.save(update_fields=["purchased_seats"])
        # Create an org-owned agent
        # Create a separate browser agent for the org-owned persistent agent
        org_browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA-Org")
        org_agent = PersistentAgent.objects.create(
            user=self.user,
            organization=org,
            name="Org Agent",
            charter="help org",
            browser_use_agent=org_browser_agent,
        )
        # Grant org a credit block
        org_credit = TaskCredit.objects.create(
            organization=org,
            credits=Decimal("1.000"),
            credits_used=Decimal("0.000"),
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            voided=False,
        )

        completion = PersistentAgentCompletion.objects.create(agent=org_agent, llm_model="gpt-4")
        step = PersistentAgentStep.objects.create(agent=org_agent, description="Org step", completion=completion)
        step.refresh_from_db()
        org_credit.refresh_from_db()

        self.assertIsNotNone(step.task_credit)
        # Ensure the linked credit is the org credit and has usage now
        self.assertEqual(step.task_credit.id, org_credit.id)
        self.assertGreater(org_credit.credits_used, 0)

    def test_reusing_completion_does_not_consume_extra_credits(self):
        completion = PersistentAgentCompletion.objects.create(agent=self.agent, llm_model="gpt-4")
        first_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Initial reasoning",
            completion=completion,
        )
        first_step.refresh_from_db()
        credit = first_step.task_credit
        self.assertIsNotNone(credit)
        credit.refresh_from_db()
        before_used = credit.credits_used

        second_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Follow-up using same completion",
            completion=completion,
        )
        second_step.refresh_from_db()
        credit.refresh_from_db()

        self.assertEqual(credit.credits_used, before_used)
        self.assertIsNone(second_step.task_credit)
        self.assertIsNone(second_step.credits_cost)
