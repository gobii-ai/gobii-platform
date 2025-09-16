from datetime import timedelta

from django.conf import settings
from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from django.utils import timezone
from unittest.mock import patch, MagicMock

from constants.plans import PlanNamesChoices


def _create_browser_agent_without_proxy(user, name: str):
    """Helper to create BrowserUseAgent without triggering proxy selection."""
    from api.models import BrowserUseAgent
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)

@tag("batch_soft_expiration")
class SoftExpirationTaskTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='soft-expire@example.com', email='soft-expire@example.com', password='password'
        )
        # Ensure soft-expiration task runs by simulating production environment.
        self._old_release_env = settings.GOBII_RELEASE_ENV
        settings.GOBII_RELEASE_ENV = 'prod'
        self.addCleanup(self._restore_release_env)

        # Ensure user has a high agent limit if quota is enforced elsewhere
        from api.models import UserQuota
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.agent_limit = 100
        quota.save()

    def _restore_release_env(self):
        settings.GOBII_RELEASE_ENV = self._old_release_env

    @patch('api.tasks.soft_expiration_task.switch_is_active', return_value=True)
    @patch('api.tasks.soft_expiration_task._send_sleep_notification')
    def test_soft_expire_free_inactive_agent(self, mock_notify: MagicMock, mock_switch):
        from api.models import PersistentAgent
        from api.tasks.soft_expiration_task import soft_expire_inactive_agents_task

        browser = _create_browser_agent_without_proxy(self.user, "browser-a")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="sleepy-agent",
            charter="Test",
            schedule="@daily",
            is_active=True,
            browser_use_agent=browser,
        )
        # Pretend it's been inactive for 8 days
        agent.last_interaction_at = timezone.now() - timedelta(days=settings.AGENT_SOFT_EXPIRATION_INACTIVITY_DAYS+1)
        agent.save(update_fields=["last_interaction_at"])

        # Run task synchronously
        expired = soft_expire_inactive_agents_task()

        self.assertEqual(expired, 1)

        agent.refresh_from_db()
        self.assertEqual(agent.life_state, PersistentAgent.LifeState.EXPIRED)
        self.assertIsNotNone(agent.last_expired_at)
        # Snapshot should contain previous cron and active schedule cleared
        self.assertEqual(agent.schedule_snapshot, "@daily")
        self.assertEqual(agent.schedule, "")
        # save() hook will handle beat sync implicitly; no direct calls asserted
        mock_notify.assert_called_once()

    @patch('api.tasks.soft_expiration_task.switch_is_active', return_value=True)
    @patch('api.tasks.soft_expiration_task._send_sleep_notification')
    def test_soft_expire_skips_pro_plan(self, mock_notify: MagicMock, mock_switch):
        from api.models import PersistentAgent, UserBilling
        from api.tasks.soft_expiration_task import soft_expire_inactive_agents_task

        # Mark user as paid
        billing, _ = UserBilling.objects.get_or_create(user=self.user)
        billing.subscription = PlanNamesChoices.STARTUP
        billing.save(update_fields=["subscription"])

        browser = _create_browser_agent_without_proxy(self.user, "browser-b")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="paid-agent",
            charter="Test",
            schedule="@daily",
            is_active=True,
            browser_use_agent=browser,
        )
        agent.last_interaction_at = timezone.now() - timedelta(days=settings.AGENT_SOFT_EXPIRATION_INACTIVITY_DAYS+1)
        agent.save(update_fields=["last_interaction_at"])

        expired = soft_expire_inactive_agents_task()

        self.assertEqual(expired, 0)
        agent.refresh_from_db()
        self.assertEqual(agent.life_state, PersistentAgent.LifeState.ACTIVE)
        mock_notify.assert_not_called()

    @patch('api.tasks.soft_expiration_task.switch_is_active', return_value=True)
    @patch('api.tasks.soft_expiration_task._send_sleep_notification')
    def test_soft_expire_skips_when_notification_already_sent(self, mock_notify: MagicMock, mock_switch):
        from api.models import PersistentAgent
        from api.tasks.soft_expiration_task import soft_expire_inactive_agents_task

        browser = _create_browser_agent_without_proxy(self.user, "browser-d")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="already-notified",
            charter="Test",
            schedule="@daily",
            is_active=True,
            browser_use_agent=browser,
        )

        # Simulate prior notification sent from preview environment.
        stale_ts = timezone.now() - timedelta(days=settings.AGENT_SOFT_EXPIRATION_INACTIVITY_DAYS+2)
        PersistentAgent.objects.filter(pk=agent.pk).update(
            last_interaction_at=stale_ts,
            sent_expiration_email=True,
        )

        expired = soft_expire_inactive_agents_task()

        self.assertEqual(expired, 1)
        mock_notify.assert_not_called()

        agent.refresh_from_db()
        self.assertEqual(agent.life_state, PersistentAgent.LifeState.EXPIRED)
        self.assertTrue(agent.sent_expiration_email)

    @patch('api.tasks.soft_expiration_task.switch_is_active', return_value=True)
    @patch('api.tasks.soft_expiration_task._send_sleep_notification')
    def test_downgrade_grace_applies(self, mock_notify: MagicMock, mock_switch):
        from api.models import PersistentAgent, UserBilling
        from api.tasks.soft_expiration_task import soft_expire_inactive_agents_task

        # Set downgraded_at to 24h ago (within 48h grace)
        billing, _ = UserBilling.objects.get_or_create(user=self.user)
        billing.subscription = PlanNamesChoices.FREE
        billing.downgraded_at = timezone.now() - timedelta(hours=settings.AGENT_SOFT_EXPIRATION_DOWNGRADE_GRACE_HOURS-24)
        billing.save(update_fields=["subscription", "downgraded_at"])

        browser = _create_browser_agent_without_proxy(self.user, "browser-c")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="grace-agent",
            charter="Test",
            schedule="@daily",
            is_active=True,
            browser_use_agent=browser,
        )
        agent.last_interaction_at = timezone.now() - timedelta(days=settings.AGENT_SOFT_EXPIRATION_INACTIVITY_DAYS+1)
        agent.save(update_fields=["last_interaction_at"])

        expired = soft_expire_inactive_agents_task()

        self.assertEqual(expired, 0)
        agent.refresh_from_db()
        self.assertEqual(agent.life_state, PersistentAgent.LifeState.ACTIVE)
        mock_notify.assert_not_called()

        # Advance beyond grace (49h ago) and try again → should expire
        billing.downgraded_at = timezone.now() - timedelta(hours=settings.AGENT_SOFT_EXPIRATION_DOWNGRADE_GRACE_HOURS+1)
        billing.save(update_fields=["downgraded_at"])
        expired2 = soft_expire_inactive_agents_task()
        self.assertEqual(expired2, 1)
        agent.refresh_from_db()
        self.assertEqual(agent.life_state, PersistentAgent.LifeState.EXPIRED)

@tag("batch_soft_expiration")
class PersistentAgentInteractionResetTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='reset-flag@example.com', email='reset-flag@example.com', password='password'
        )

    def test_last_interaction_reset_flag(self):
        from api.models import PersistentAgent

        browser = _create_browser_agent_without_proxy(self.user, "browser-reset")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="reset-agent",
            charter="Test",
            schedule="@daily",
            is_active=True,
            browser_use_agent=browser,
        )

        agent.sent_expiration_email = True
        agent.save(update_fields=["sent_expiration_email"])

        # Update last_interaction_at to simulate user waking the agent.
        new_ts = timezone.now()
        agent.last_interaction_at = new_ts
        agent.save(update_fields=["last_interaction_at"])

        agent.refresh_from_db()
        self.assertFalse(agent.sent_expiration_email)
