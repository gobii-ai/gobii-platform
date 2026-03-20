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
        self.assertFalse(agent.is_active)
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
        self.assertTrue(agent.is_active)
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
        self.assertFalse(agent.is_active)
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
        self.assertTrue(agent.is_active)
        mock_notify.assert_not_called()

        # Advance beyond grace (49h ago) and try again → should expire
        billing.downgraded_at = timezone.now() - timedelta(hours=settings.AGENT_SOFT_EXPIRATION_DOWNGRADE_GRACE_HOURS+1)
        billing.save(update_fields=["downgraded_at"])
        expired2 = soft_expire_inactive_agents_task()
        self.assertEqual(expired2, 1)
        agent.refresh_from_db()
        self.assertEqual(agent.life_state, PersistentAgent.LifeState.EXPIRED)
        self.assertFalse(agent.is_active)

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


@tag("batch_soft_expiration")
class IsExemptFromSoftExpirationTests(TestCase):
    """Unit tests for the _is_exempt_from_soft_expiration helper."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='exempt-test@example.com', email='exempt-test@example.com', password='password'
        )
        from api.models import UserQuota
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.agent_limit = 100
        quota.save()

    def _make_agent(self, name="exempt-agent"):
        browser = _create_browser_agent_without_proxy(self.user, f"browser-{name}")
        from api.models import PersistentAgent
        return PersistentAgent.objects.create(
            user=self.user,
            name=name,
            charter="Test",
            schedule="@daily",
            is_active=True,
            browser_use_agent=browser,
        )

    def test_free_plan_no_grace_is_not_exempt(self):
        """A free-plan user with no downgrade grace is not exempt."""
        from api.models import PersistentAgent
        from api.tasks.soft_expiration_task import _is_exempt_from_soft_expiration
        agent = self._make_agent("free-no-grace")
        agent = PersistentAgent.objects.select_related("user", "user__billing", "organization").get(pk=agent.pk)
        self.assertFalse(_is_exempt_from_soft_expiration(agent))

    def test_paid_plan_is_exempt(self):
        """A paid-plan user's agent is exempt from soft-expiration."""
        from api.models import PersistentAgent, UserBilling
        from api.tasks.soft_expiration_task import _is_exempt_from_soft_expiration
        billing, _ = UserBilling.objects.get_or_create(user=self.user)
        billing.subscription = PlanNamesChoices.STARTUP
        billing.save(update_fields=["subscription"])

        agent = self._make_agent("paid-exempt")
        agent = PersistentAgent.objects.select_related("user", "user__billing", "organization").get(pk=agent.pk)
        self.assertTrue(_is_exempt_from_soft_expiration(agent))

    def test_free_plan_within_grace_is_exempt(self):
        """A free-plan user within the downgrade grace window is exempt."""
        from api.models import PersistentAgent, UserBilling
        from api.tasks.soft_expiration_task import _is_exempt_from_soft_expiration
        billing, _ = UserBilling.objects.get_or_create(user=self.user)
        billing.subscription = PlanNamesChoices.FREE
        billing.downgraded_at = timezone.now() - timedelta(hours=settings.AGENT_SOFT_EXPIRATION_DOWNGRADE_GRACE_HOURS - 1)
        billing.save(update_fields=["subscription", "downgraded_at"])

        agent = self._make_agent("grace-exempt")
        agent = PersistentAgent.objects.select_related("user", "user__billing", "organization").get(pk=agent.pk)
        self.assertTrue(_is_exempt_from_soft_expiration(agent))

    def test_free_plan_past_grace_is_not_exempt(self):
        """A free-plan user past the grace window is not exempt."""
        from api.models import PersistentAgent, UserBilling
        from api.tasks.soft_expiration_task import _is_exempt_from_soft_expiration
        billing, _ = UserBilling.objects.get_or_create(user=self.user)
        billing.subscription = PlanNamesChoices.FREE
        billing.downgraded_at = timezone.now() - timedelta(hours=settings.AGENT_SOFT_EXPIRATION_DOWNGRADE_GRACE_HOURS + 1)
        billing.save(update_fields=["subscription", "downgraded_at"])

        agent = self._make_agent("past-grace")
        agent = PersistentAgent.objects.select_related("user", "user__billing", "organization").get(pk=agent.pk)
        self.assertFalse(_is_exempt_from_soft_expiration(agent))

    def test_no_billing_record_is_not_exempt(self):
        """An agent with no billing record defaults to free plan and is not exempt."""
        from api.models import PersistentAgent, UserBilling
        from api.tasks.soft_expiration_task import _is_exempt_from_soft_expiration
        agent = self._make_agent("no-billing")
        # Ensure no billing record exists
        UserBilling.objects.filter(user=self.user).delete()
        agent = PersistentAgent.objects.select_related("user", "user__billing", "organization").get(pk=agent.pk)
        self.assertFalse(_is_exempt_from_soft_expiration(agent))


@tag("batch_soft_expiration")
class SoftExpireIsActiveStateTests(TestCase):
    """Verify that is_active is set to False when an agent soft-expires."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='is-active-test@example.com', email='is-active-test@example.com', password='password'
        )
        self._old_release_env = settings.GOBII_RELEASE_ENV
        settings.GOBII_RELEASE_ENV = 'prod'
        self.addCleanup(self._restore_release_env)

        from api.models import UserQuota
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.agent_limit = 100
        quota.save()

    def _restore_release_env(self):
        settings.GOBII_RELEASE_ENV = self._old_release_env

    @patch('api.tasks.soft_expiration_task.switch_is_active', return_value=True)
    @patch('api.tasks.soft_expiration_task._send_sleep_notification')
    def test_is_active_set_false_on_expiry(self, mock_notify, mock_switch):
        """is_active must be False in the DB after a successful soft-expiration."""
        from api.models import PersistentAgent
        from api.tasks.soft_expiration_task import soft_expire_inactive_agents_task

        browser = _create_browser_agent_without_proxy(self.user, "browser-ia-1")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="ia-agent",
            charter="Test",
            schedule="@daily",
            is_active=True,
            browser_use_agent=browser,
        )
        PersistentAgent.objects.filter(pk=agent.pk).update(
            last_interaction_at=timezone.now() - timedelta(days=settings.AGENT_SOFT_EXPIRATION_INACTIVITY_DAYS + 2),
        )

        expired = soft_expire_inactive_agents_task()
        self.assertEqual(expired, 1)

        agent.refresh_from_db()
        self.assertFalse(agent.is_active, "Soft-expired agent must have is_active=False in the database.")
        self.assertEqual(agent.life_state, PersistentAgent.LifeState.EXPIRED)

    @patch('api.tasks.soft_expiration_task.switch_is_active', return_value=True)
    @patch('api.tasks.soft_expiration_task._send_sleep_notification')
    def test_is_active_stays_true_when_not_expired(self, mock_notify, mock_switch):
        """is_active must remain True for recently-active agents that are skipped."""
        from api.models import PersistentAgent
        from api.tasks.soft_expiration_task import soft_expire_inactive_agents_task

        browser = _create_browser_agent_without_proxy(self.user, "browser-ia-2")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="recent-agent",
            charter="Test",
            schedule="@daily",
            is_active=True,
            browser_use_agent=browser,
        )
        # Just interacted — well within the inactivity cutoff
        agent.last_interaction_at = timezone.now() - timedelta(days=1)
        agent.save(update_fields=["last_interaction_at"])

        expired = soft_expire_inactive_agents_task()
        self.assertEqual(expired, 0)

        agent.refresh_from_db()
        self.assertTrue(agent.is_active)
        self.assertEqual(agent.life_state, PersistentAgent.LifeState.ACTIVE)

    @patch('api.tasks.soft_expiration_task.switch_is_active', return_value=True)
    @patch('api.tasks.soft_expiration_task._send_sleep_notification')
    def test_multiple_agents_all_expired_have_is_active_false(self, mock_notify, mock_switch):
        """All expired agents in a batch run must have is_active=False."""
        from api.models import PersistentAgent
        from api.tasks.soft_expiration_task import soft_expire_inactive_agents_task

        stale_ts = timezone.now() - timedelta(days=settings.AGENT_SOFT_EXPIRATION_INACTIVITY_DAYS + 3)
        agents = []
        for i in range(3):
            browser = _create_browser_agent_without_proxy(self.user, f"browser-multi-{i}")
            agent = PersistentAgent.objects.create(
                user=self.user,
                name=f"multi-agent-{i}",
                charter="Test",
                schedule="@daily",
                is_active=True,
                browser_use_agent=browser,
            )
            PersistentAgent.objects.filter(pk=agent.pk).update(last_interaction_at=stale_ts)
            agents.append(agent)

        expired = soft_expire_inactive_agents_task()
        self.assertEqual(expired, 3)

        for agent in agents:
            agent.refresh_from_db()
            self.assertFalse(agent.is_active, f"Agent {agent.name} must have is_active=False after expiry.")
            self.assertEqual(agent.life_state, PersistentAgent.LifeState.EXPIRED)
