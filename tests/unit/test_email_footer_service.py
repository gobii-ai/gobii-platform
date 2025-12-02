from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.comms.email_footer_service import append_footer_if_needed
from api.models import (
    BrowserUseAgent,
    Organization,
    PersistentAgent,
    PersistentAgentEmailFooter,
)
from constants.plans import PlanNamesChoices

User = get_user_model()


@tag("batch_email_footer")
class PersistentAgentEmailFooterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="footer@example.com",
            email="footer@example.com",
            password="test-password",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Footer Browser Agent",
        )
        self.footer = PersistentAgentEmailFooter.objects.create(
            name="Test Footer",
            html_content="<table><tr><td>HTML Footer</td></tr></table>",
            text_content="Plain footer text",
        )

    def _create_agent(self, **overrides):
        data = {
            "user": self.user,
            "name": overrides.pop("name", "Footer Agent"),
            "charter": overrides.pop("charter", "Help users with testing."),
            "browser_use_agent": overrides.pop("browser_use_agent", self.browser_agent),
        }
        data.update(overrides)
        return PersistentAgent.objects.create(**data)

    def test_footer_added_for_free_user_plan(self):
        agent = self._create_agent()

        html, text = append_footer_if_needed(agent, "<p>Hello</p>", "Hello")

        self.assertIn("HTML Footer", html)
        self.assertIn("Plain footer text", text)

    def test_footer_added_for_org_without_seats(self):
        org = Organization.objects.create(
            name="Seatless Org",
            slug="seatless-org",
            plan="org_team",
            created_by=self.user,
        )
        billing = org.billing
        billing.purchased_seats = 1
        billing.subscription = PlanNamesChoices.ORG_TEAM
        billing.save(update_fields=["purchased_seats", "subscription"])

        agent = self._create_agent(organization=org, name="Org Agent")

        billing.purchased_seats = 0
        billing.save(update_fields=["purchased_seats"])

        html, text = append_footer_if_needed(agent, "<p>Hello</p>", "Hello")

        self.assertIn("HTML Footer", html)
        self.assertIn("Plain footer text", text)

    def test_footer_skipped_for_paid_plan(self):
        billing = self.user.billing
        billing.subscription = PlanNamesChoices.STARTUP
        billing.save(update_fields=["subscription"])

        agent = self._create_agent(name="Paid Agent")

        html, text = append_footer_if_needed(agent, "<p>Hello</p>", "Hello")

        self.assertNotIn("HTML Footer", html)
        self.assertNotIn("Plain footer text", text)
