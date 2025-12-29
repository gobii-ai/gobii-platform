from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings
from unittest.mock import patch

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

    @tag("batch_email_footer")
    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("api.agent.comms.email_footer_service.switch_is_active", return_value=True)
    @patch("api.agent.comms.email_footer_service.get_redis_client")
    def test_throttle_footer_replaces_default_footer_once(self, mock_get_redis, _mock_switch):
        class _FakeRedis:
            def __init__(self):
                self._store = {}

            def get(self, key):
                return self._store.get(key)

            def set(self, key, value, ex=None, nx=None):
                if nx and key in self._store:
                    return False
                self._store[key] = value
                return True

            def delete(self, key):
                self._store.pop(key, None)
                return 1

            def exists(self, key):
                return 1 if key in self._store else 0

        fake_redis = _FakeRedis()
        mock_get_redis.return_value = fake_redis

        agent = self._create_agent()
        from api.services.cron_throttle import cron_throttle_pending_footer_key, cron_throttle_footer_cooldown_key

        pending_key = cron_throttle_pending_footer_key(str(agent.id))
        fake_redis.set(pending_key, "1")

        html, text = append_footer_if_needed(agent, "<p>Hello</p>", "Hello")
        self.assertIn("🥺", html)
        self.assertIn("Upgrade", html)
        self.assertIn("/subscribe/pro/", html)
        self.assertNotIn("HTML Footer", html)
        self.assertNotIn("Plain footer text", text)

        self.assertFalse(fake_redis.get(pending_key))
        self.assertTrue(fake_redis.get(cron_throttle_footer_cooldown_key(str(agent.id))))

        # Next email falls back to normal footer
        html2, text2 = append_footer_if_needed(agent, "<p>Hello</p>", "Hello")
        self.assertIn("HTML Footer", html2)
        self.assertIn("Plain footer text", text2)
