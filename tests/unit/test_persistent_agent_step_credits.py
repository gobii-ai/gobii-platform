from decimal import Decimal
from django.test import TestCase, override_settings, tag
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentStep,
    TaskCredit,
    Organization,
)


User = get_user_model()


@tag("batch_pa_step_credits")
class PersistentAgentStepCreditsTests(TestCase):
    def setUp(self):
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
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Test step",
            llm_model="gpt-4",
        )
        step.refresh_from_db()

        # Should link to a consumed credit block and set default cost
        self.assertIsNotNone(step.task_credit)
        self.assertIsNotNone(step.credits_cost)
        # The linked credit should have non-zero usage
        credit = step.task_credit
        credit.refresh_from_db()
        self.assertGreater(credit.credits_used, 0)

    @override_settings(CREDITS_PER_TASK=Decimal("0.1"))
    def test_fractional_credit_consumption(self):
        # Find the first valid credit block for the user
        credit = TaskCredit.objects.filter(user=self.user, expiration_date__gte=timezone.now(), voided=False).order_by("expiration_date").first()
        self.assertIsNotNone(credit)
        before_used = credit.credits_used

        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Fractional step",
            llm_model="gpt-4",
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

        step = PersistentAgentStep.objects.create(
            agent=org_agent,
            description="Org step",
            llm_model="gpt-4",
        )
        step.refresh_from_db()
        org_credit.refresh_from_db()

        self.assertIsNotNone(step.task_credit)
        # Ensure the linked credit is the org credit and has usage now
        self.assertEqual(step.task_credit.id, org_credit.id)
        self.assertGreater(org_credit.credits_used, 0)
