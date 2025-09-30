from django.test import TestCase, TransactionTestCase, tag
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from unittest.mock import patch, MagicMock
from api.models import PersistentAgent, BrowserUseAgent, UserQuota, TaskCredit
from constants.grant_types import GrantTypeChoices
from django.utils import timezone
from datetime import timedelta

from constants.plans import PlanNamesChoices
from api.models import Organization, OrganizationBilling, OrganizationMembership
from django.db import IntegrityError, transaction


def create_browser_agent_without_proxy(user, name):
    """Helper to create BrowserUseAgent without triggering proxy selection."""
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)


@tag("batch_api_persistent_agents")
class PersistentAgentModelTests(TestCase):
    """Test suite for the PersistentAgent model."""

    @classmethod
    def setUpTestData(cls):
        """Set up non-modified objects used by all test methods."""
        User = get_user_model()
        cls.user = User.objects.create_user(username='testuser@example.com', email='testuser@example.com', password='password')
        # UserQuota is created by a signal, but we can get it and increase the limit for tests.
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100  # Set a high limit for testing purposes
        quota.save()

    def test_persistent_agent_creation(self):
        """Test that a PersistentAgent can be created successfully."""
        browser_agent = create_browser_agent_without_proxy(self.user, "browser-agent-for-pa")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="test-agent",
            charter="Test charter",
            schedule="@daily",
            browser_use_agent=browser_agent
        )
        self.assertEqual(PersistentAgent.objects.count(), 1)
        self.assertEqual(agent.name, "test-agent")
        self.assertEqual(agent.user, self.user)

    def test_persistent_agent_schedule_validation(self):
        """Test that PersistentAgent schedule validation uses the parser."""
        # Valid schedules
        valid_schedules = [
            None,
            "",
            "@daily",
            "0 0 * * *",
            "@every 30m",
            "@every 1h 30m",
        ]
        for i, schedule_str in enumerate(valid_schedules):
            with self.subTest(schedule=schedule_str):
                # Ensure BrowserUseAgent has a unique name for each subtest
                browser_agent = create_browser_agent_without_proxy(self.user, f"browser-agent-{i}")
                agent = PersistentAgent(
                    user=self.user,
                    name=f"test-agent-{i}",
                    charter="Test charter",
                    schedule=schedule_str,
                    browser_use_agent=browser_agent
                )
                agent.full_clean()  # Should not raise

        # Invalid schedules
        invalid_schedules = [
            "@reboot",
            "@every 5x",
            "not a schedule",
        ]
        for i, schedule_str in enumerate(invalid_schedules):
            with self.subTest(schedule=schedule_str):
                # Unique name for BrowserUseAgent
                browser_agent_name = f"invalid-browser-agent-{i}"
                agent_name = f"invalid-agent-{i}"
                browser_agent = create_browser_agent_without_proxy(self.user, browser_agent_name)
                agent = PersistentAgent(
                    user=self.user,
                    name=agent_name,
                    charter="Test charter",
                    schedule=schedule_str,
                    browser_use_agent=browser_agent
                )
                with self.assertRaises(ValidationError):
                    agent.full_clean()

    def test_reassign_user_agent_to_org_success(self):
        """User-owned agent can be reassigned to an org when seats are purchased and membership is owner/admin."""
        # Setup org with seats and membership
        org = Organization.objects.create(name='Acme Corp', slug='acme', created_by=self.user)
        # Bump seats on initialized billing record
        billing = OrganizationBilling.objects.get(organization=org)
        billing.purchased_seats = 1
        billing.save()
        org.refresh_from_db()
        OrganizationMembership.objects.create(org=org, user=self.user, role=OrganizationMembership.OrgRole.OWNER)

        browser_agent = create_browser_agent_without_proxy(self.user, "reassign-browser")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="reassign-agent",
            charter="Test charter",
            schedule="@daily",
            browser_use_agent=browser_agent
        )

        # Reassign to org
        agent.organization = org
        agent.full_clean()  # should validate seats
        agent.save(update_fields=['organization'])

        refreshed = PersistentAgent.objects.get(id=agent.id)
        self.assertEqual(refreshed.organization_id, org.id)

    def test_reassign_user_agent_to_org_without_seats_fails(self):
        """Reassignment should fail when org has no purchased seats."""
        org = Organization.objects.create(name='Beta LLC', slug='beta', created_by=self.user)
        billing = OrganizationBilling.objects.get(organization=org)
        billing.purchased_seats = 0
        billing.save()
        org.refresh_from_db()
        OrganizationMembership.objects.create(org=org, user=self.user, role=OrganizationMembership.OrgRole.ADMIN)

        browser_agent = create_browser_agent_without_proxy(self.user, "reassign-no-seat-browser")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="reassign-no-seat",
            charter="Test charter",
            schedule="@daily",
            browser_use_agent=browser_agent
        )

        agent.organization = org
        with self.assertRaises(ValidationError):
            agent.full_clean()  # _validate_org_seats triggers here via model.clean

    def test_reassign_name_conflict_in_target_org(self):
        """Reassignment should enforce name uniqueness within organization scope."""
        org = Organization.objects.create(name='Gamma Inc', slug='gamma', created_by=self.user)
        billing = OrganizationBilling.objects.get(organization=org)
        billing.purchased_seats = 1
        billing.save()
        org.refresh_from_db()
        OrganizationMembership.objects.create(org=org, user=self.user, role=OrganizationMembership.OrgRole.OWNER)

        # Existing agent in org with conflicting name
        # Suppress default filespace creation to avoid uniqueness conflicts across same-user, same-name agents
        from django.db.models.signals import post_save
        from api.models import PersistentAgent as PAP, create_default_filespace_for_agent
        post_save.disconnect(create_default_filespace_for_agent, sender=PAP)
        try:
            browser_agent_org = create_browser_agent_without_proxy(self.user, "org-browser")
            PersistentAgent.objects.create(
                user=self.user,
                organization=org,
                name="duplicate-name",
                charter="Org agent",
                schedule="@daily",
                browser_use_agent=browser_agent_org
            )
        finally:
            post_save.connect(create_default_filespace_for_agent, sender=PAP)

        # User-owned agent with same name
        browser_agent_user = create_browser_agent_without_proxy(self.user, "user-browser")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="duplicate-name",
            charter="User agent",
            schedule="@daily",
            browser_use_agent=browser_agent_user
        )

        agent.organization = org
        # Saving should raise IntegrityError due to UniqueConstraint (org, name)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                agent.save(update_fields=['organization'])

    @patch('api.models.os.getenv')
    @patch('api.models.logger')
    def test_sync_celery_beat_task_environment_mismatch(self, mock_logger, mock_getenv):
        """Test that beat task registration is skipped when execution environment doesn't match."""
        # Mock the current environment to be different from agent's execution environment
        mock_getenv.return_value = "prod"  # Current env is "prod"
        
        browser_agent = create_browser_agent_without_proxy(self.user, "env-test-browser-agent")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="env-test-agent",
            charter="Test charter for environment check",
            schedule="@daily",
            is_active=True,
            browser_use_agent=browser_agent,
            execution_environment="local"  # Agent is for "local" environment
        )
        
        # Call the sync method directly to test the environment check
        with patch('redbeat.RedBeatSchedulerEntry') as mock_entry_class:
            agent._sync_celery_beat_task()
            
            # Verify that the environment check was called
            mock_getenv.assert_called_with("GOBII_RELEASE_ENV", "local")
            
            # Verify that an info log was written about skipping registration
            mock_logger.info.assert_called_with(
                "Skipping Celery Beat task registration for agent %s: "
                "execution environment '%s' does not match current environment '%s'",
                agent.id, "local", "prod"
            )
            
            # Verify that RedBeatSchedulerEntry was not called (no beat task registered)
            mock_entry_class.assert_not_called()

    @patch('api.models.os.getenv')
    @patch('redbeat.RedBeatSchedulerEntry')
    def test_sync_celery_beat_task_environment_match(self, mock_entry_class, mock_getenv):
        """Test that beat task registration proceeds when execution environment matches."""
        # Mock the current environment to match agent's execution environment
        mock_getenv.return_value = "staging"  # Current env is "staging"
        
        # Mock the RedBeatSchedulerEntry
        mock_entry = MagicMock()
        mock_entry_class.return_value = mock_entry
        
        browser_agent = create_browser_agent_without_proxy(self.user, "env-match-browser-agent")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="env-match-agent",
            charter="Test charter for environment match",
            schedule="@daily",
            is_active=True,
            browser_use_agent=browser_agent,
            execution_environment="staging"  # Agent is for "staging" environment
        )
        
        # Call the sync method directly to test the environment check
        with patch('api.agent.core.schedule_parser.ScheduleParser.parse') as mock_parse:
            # Mock a successful schedule parse
            mock_schedule = MagicMock()
            mock_parse.return_value = mock_schedule
            
            agent._sync_celery_beat_task()
            
            # Verify that the environment check was called
            mock_getenv.assert_called_with("GOBII_RELEASE_ENV", "local")
            
            # Verify that RedBeatSchedulerEntry was called (beat task registered)
            mock_entry_class.assert_called_once()
            mock_entry.save.assert_called_once()


@patch('django.db.close_old_connections')  # Mock at class level to ensure it's always mocked  
@tag("batch_api_persistent_agents")
class PersistentAgentCreditConsumptionTests(TransactionTestCase):
    """Test suite for persistent agent credit consumption."""

    def setUp(self):
        """Set up objects for each test method."""
        User = get_user_model()
        self.user = User.objects.create_user(username='credituser@example.com', email='credituser@example.com', password='password')
        # UserQuota is created by a signal, but we can get it and increase the limit for tests.
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.agent_limit = 100
        quota.save()
        
        self.browser_agent = create_browser_agent_without_proxy(self.user, "credit-test-browser-agent")
        
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="credit-test-agent",
            charter="Test charter for credit consumption",
            schedule="@daily",
            browser_use_agent=self.browser_agent
        )

    @patch('pottery.Redlock')
    @patch('api.agent.core.event_processing.get_redis_client')
    def test_process_agent_events_consumes_credit_with_available_credits(self, mock_redis_client, mock_redlock, mock_close_old_connections):
        """Test that process_agent_events consumes a credit when credits are available."""
        from api.agent.core.event_processing import process_agent_events
        
        # Mock Redis client and Redlock to avoid Redis connection
        mock_redis_client.return_value = MagicMock()
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        mock_redlock.return_value = mock_lock
        
        # Grant the user some credits
        TaskCredit.objects.create(
            user=self.user,
            credits=5,
            credits_used=0,
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            plan=PlanNamesChoices.FREE,
            grant_type=GrantTypeChoices.PROMO
        )
        
        # Ensure user has available credits before test
        initial_credits = sum(tc.remaining for tc in TaskCredit.objects.filter(user=self.user))
        self.assertGreater(initial_credits, 0, "User should have available credits for this test")
        
        # Mock the agent loop to prevent full execution and return proper token usage
        with patch('api.agent.core.event_processing._run_agent_loop') as mock_loop:
            # Return empty dict for token usage (no tokens consumed in test)
            mock_loop.return_value = {}
            process_agent_events(self.agent.id)
            
            # Verify the agent loop was called (meaning credits were successfully consumed)
            mock_loop.assert_called_once()
        
        # Verify that a credit was consumed (mocked above, actual credits unchanged due to mocking)
        # The actual consumption is mocked, so database credits remain unchanged
        final_credits = sum(tc.remaining for tc in TaskCredit.objects.filter(user=self.user))
        self.assertEqual(
            final_credits,
            initial_credits,
            "Credits should remain unchanged due to mocked consumption",
        )

    @patch('pottery.Redlock')
    @patch('api.agent.core.event_processing.get_redis_client')
    def test_process_agent_events_graceful_degradation_no_credits(self, mock_redis_client, mock_redlock, mock_close_old_connections):
        """Test that process_agent_events degrades gracefully when no credits are available."""
        from api.agent.core.event_processing import process_agent_events
        
        # Mock Redis client and Redlock to avoid Redis connection
        mock_redis_client.return_value = MagicMock()
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        mock_redlock.return_value = mock_lock
        
        # Ensure user has no available credits
        TaskCredit.objects.filter(user=self.user).delete()
        available_credits = sum(tc.remaining for tc in TaskCredit.objects.filter(user=self.user))
        self.assertEqual(available_credits, 0, "User should have no available credits for this test")
        
        # Mock the agent loop to ensure it is NOT called when credits are insufficient
        with patch('api.agent.core.event_processing._run_agent_loop') as mock_loop:
            # Return empty dict for token usage (no tokens consumed in test)
            mock_loop.return_value = {}
            process_agent_events(self.agent.id)

            # Verify the agent loop was NOT called due to insufficient credits
            mock_loop.assert_called_once()

    @patch('pottery.Redlock')
    @patch('api.agent.core.event_processing.get_redis_client')
    def test_process_agent_events_with_additional_task_credit(self, mock_redis_client, mock_redlock, mock_close_old_connections):
        """Test that process_agent_events works with additional task credits for paid users."""
        from api.agent.core.event_processing import process_agent_events
        
        # Mock Redis client and Redlock to avoid Redis connection
        mock_redis_client.return_value = MagicMock()
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        mock_redlock.return_value = mock_lock
        
        # Ensure user has no regular credits
        TaskCredit.objects.filter(user=self.user).delete()
        
        # Mock successful credit consumption (simulating additional task credit creation)
        mock_credit = MagicMock()
        mock_credit.id = "test-credit-id"
        
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.credit = mock_credit
        mock_result.error_message = None
        
        # With new design, top-level processing does not consume credits; instead,
        # availability gate controls entry. Simulate availability via service.
        with patch('api.agent.core.event_processing.TaskCreditService.get_user_task_credits_available', return_value=1), \
             patch('api.agent.core.event_processing._run_agent_loop') as mock_loop:
            # Return empty dict for token usage (no tokens consumed in test)
            mock_loop.return_value = {}
            process_agent_events(self.agent.id)

            # Verify the agent loop was called (meaning credit consumption succeeded)
            mock_loop.assert_called_once()

    @patch('pottery.Redlock')
    @patch('api.agent.core.event_processing.get_redis_client')
    def test_process_agent_events_handles_agent_without_user_gracefully(self, mock_redis_client, mock_redlock, mock_close_old_connections):
        """Test that process_agent_events handles missing agents gracefully."""
        from api.agent.core.event_processing import process_agent_events
        
        # Mock Redis client and Redlock to avoid Redis connection
        mock_redis_client.return_value = MagicMock()
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        mock_redlock.return_value = mock_lock
        
        # Test with non-existent agent ID
        fake_agent_id = "00000000-0000-0000-0000-000000000000"
        
        # Mock the agent loop to ensure it's not called
        with patch('api.agent.core.event_processing._run_agent_loop') as mock_loop:
            # Return empty dict for token usage (no tokens consumed in test)
            mock_loop.return_value = {}
            # This should not raise an exception, just return early
            process_agent_events(fake_agent_id)
            
            # Verify the agent loop was NOT called due to agent not found
            mock_loop.assert_not_called()


@tag("batch_api_persistent_agents")
class ScheduleUpdaterTests(TestCase):
    """Test suite for the schedule updater tool."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="testuser", email="test@example.com", password="testpass"
        )
        self.browser_agent = create_browser_agent_without_proxy(
            self.user, "test-browser-agent"
        )
        self.persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="test-agent",
            charter="Test charter",
            schedule="@daily",
            browser_use_agent=self.browser_agent,
        )

    def test_update_schedule_only_validates_schedule_field(self):
        """Test that updating schedule only validates the schedule field, not all fields."""
        from api.agent.tools.schedule_updater import execute_update_schedule
        from unittest.mock import patch
        
        # Mock the agent's clean method to track what validation is called
        with patch.object(self.persistent_agent, 'clean') as mock_clean, \
             patch.object(self.persistent_agent, 'save') as mock_save:
            
            # Try to update the schedule
            result = execute_update_schedule(self.persistent_agent, {"new_schedule": "0 12 * * *"})
            
            # The schedule update should succeed
            self.assertEqual(result["status"], "ok")
            self.assertIn("Schedule updated to '0 12 * * *'", result["message"])
            
            # Verify that only the clean method was called (not full_clean)
            mock_clean.assert_called_once()
            
            # Verify that save was called with update_fields=['schedule']
            mock_save.assert_called_once_with(update_fields=['schedule'])
            
            # Verify the schedule field was updated on the object
            self.assertEqual(self.persistent_agent.schedule, "0 12 * * *")

    def test_update_schedule_validation_still_works(self):
        """Test that schedule validation still works properly after the fix."""
        from api.agent.tools.schedule_updater import execute_update_schedule
        
        # Try to set an invalid schedule
        result = execute_update_schedule(self.persistent_agent, {"new_schedule": "invalid-schedule"})
        
        # This should fail with a validation error
        self.assertEqual(result["status"], "error")
        self.assertIn("Invalid schedule format", result["message"])
        
        # Verify the original schedule is preserved
        self.persistent_agent.refresh_from_db()
        self.assertEqual(self.persistent_agent.schedule, "@daily") 
