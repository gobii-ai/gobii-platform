import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, tag

from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    UserPhoneNumber,
    UserQuota,
)

PERSISTENT_AGENT_BASE_URL = '/api/v1/agents/'

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

    def test_persistent_agent_blank_charter_allowed(self):
        """PersistentAgent should allow an empty charter during validation."""
        browser_agent = create_browser_agent_without_proxy(self.user, "browser-agent-blank-charter")
        agent = PersistentAgent(
            user=self.user,
            name="blank-charter-agent",
            charter="",
            browser_use_agent=browser_agent,
        )
        agent.full_clean()  # Should not raise
        agent.save()
        self.assertEqual(agent.charter, "")

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

@tag("batch_api_persistent_agents")
class AgentEventProcessingTests(TestCase):
    """Test suite for the function that processes agent events."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='event-user@example.com', email='event-user@example.com', password='password')
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.agent_limit = 100
        quota.save()
        self.browser_agent = create_browser_agent_without_proxy(self.user, "event-browser-agent")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="event-agent",
            charter="Event charter",
            schedule="@daily",
            browser_use_agent=self.browser_agent
        )

    @patch('api.agent.core.event_processing.close_old_connections')
    def test_process_agent_events_consumes_credits_when_available(self, mock_close_old_connections):
        """Process agent events should consume credits when available."""
        from api.agent.core.event_processing import process_agent_events

        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        mock_lock.release.return_value = None

        fake_redis = MagicMock()
        fake_redis.register_script.return_value = MagicMock()

        with patch('api.agent.core.event_processing.TaskCreditService.get_user_task_credits_available', return_value=1), \
             patch('api.agent.core.event_processing._process_agent_events_locked') as mock_locked, \
             patch('api.agent.core.event_processing.get_redis_client', return_value=fake_redis), \
             patch('pottery.Redlock', return_value=mock_lock):
            process_agent_events(self.agent.id)

        mock_locked.assert_called_once()

    @patch('api.agent.core.event_processing.close_old_connections')
    @patch('pottery.Redlock')
    @patch('api.agent.core.event_processing.get_redis_client')
    def test_process_agent_events_handles_agent_without_user_gracefully(self, mock_redis_client, mock_redlock, mock_close_old_connections):
        """Test that process_agent_events handles missing agents gracefully."""
        from api.agent.core.event_processing import process_agent_events

        # Mock Redis client and Redlock to avoid Redis connection
        mock_redis_client.return_value = MagicMock()
        mock_redis_client.return_value.register_script.return_value = MagicMock()
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


@tag("batch_api_persistent_agents")
class PersistentAgentAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='persistent-api-owner',
            email='owner@example.com',
            password='password123',
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)
        self._delay_patcher = patch('api.agent.tasks.process_agent_events_task.delay')
        self.process_events_mock = self._delay_patcher.start()
        self.addCleanup(self._delay_patcher.stop)
        self._on_commit_patcher = patch('api.serializers.transaction.on_commit', side_effect=lambda fn: fn())
        self.on_commit_mock = self._on_commit_patcher.start()
        self.addCleanup(self._on_commit_patcher.stop)
        self._analytics_patcher = patch('api.views.Analytics.track_event')
        self.analytics_mock = self._analytics_patcher.start()
        self.addCleanup(self._analytics_patcher.stop)

    def _create_agent_via_api(self, payload: dict | None = None) -> dict:
        data = {
            'name': 'API Persistent Agent',
            'charter': 'Automate product updates',
            'schedule': '0 9 * * 1',
        }
        if payload:
            data.update(payload)

        response = self.client.post(PERSISTENT_AGENT_BASE_URL, data=json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_create_agent_via_api(self):
        payload = self._create_agent_via_api()
        agent = PersistentAgent.objects.get(id=payload['id'])
        self.assertEqual(agent.name, 'API Persistent Agent')
        self.assertEqual(agent.charter, 'Automate product updates')
        self.assertEqual(agent.schedule, '0 9 * * 1')
        self.assertTrue(agent.is_active)
        self.assertEqual(agent.user_id, self.user.id)
        self.process_events_mock.assert_called_with(str(agent.id))

    def test_create_agent_duplicate_name_returns_validation_error(self):
        self._create_agent_via_api({'name': 'Duplicate Agent'})

        response = self.client.post(
            PERSISTENT_AGENT_BASE_URL,
            data=json.dumps({
                'name': 'Duplicate Agent',
                'charter': 'Automate product updates',
                'schedule': '0 9 * * 1',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400, response.content)
        payload = response.json()
        self.assertIn('name', payload)
        self.assertTrue(any('already' in msg.lower() for msg in payload['name']))
        self.assertEqual(PersistentAgent.objects.count(), 1)
        self.assertEqual(BrowserUseAgent.objects.count(), 1)

    def test_list_excludes_eval_agents(self):
        visible = self._create_agent_via_api({'name': 'Visible Agent'})

        eval_browser = create_browser_agent_without_proxy(self.user, "eval-browser")
        eval_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Eval Agent Should Hide",
            charter="Eval-only agent",
            browser_use_agent=eval_browser,
            execution_environment="eval",
        )

        response = self.client.get(PERSISTENT_AGENT_BASE_URL)
        self.assertEqual(response.status_code, 200, response.content)

        results = response.json().get('results', [])
        returned_ids = {row.get('id') for row in results}

        self.assertIn(visible['id'], returned_ids)
        self.assertNotIn(str(eval_agent.id), returned_ids)

    def test_create_agent_with_email_preferred_endpoint(self):
        payload = self._create_agent_via_api({'preferred_contact_endpoint': 'email'})
        agent = PersistentAgent.objects.get(id=payload['id'])
        self.assertIsNotNone(agent.preferred_contact_endpoint)
        self.assertEqual(agent.preferred_contact_endpoint.channel, CommsChannel.EMAIL)
        self.assertEqual(agent.preferred_contact_endpoint.address, self.user.email)
        self.assertEqual(agent.preferred_contact_endpoint.owner_agent, None)

    def test_create_agent_with_sms_preferred_endpoint(self):
        UserPhoneNumber.objects.create(
            user=self.user,
            phone_number='+15550000001',
            is_primary=True,
            is_verified=True,
        )
        payload = self._create_agent_via_api({'preferred_contact_endpoint': 'sms'})
        agent = PersistentAgent.objects.get(id=payload['id'])
        self.assertIsNotNone(agent.preferred_contact_endpoint)
        self.assertEqual(agent.preferred_contact_endpoint.channel, CommsChannel.SMS)
        self.assertEqual(agent.preferred_contact_endpoint.address, '+15550000001')

    def test_create_agent_with_sms_preferred_endpoint_missing_verified_number_rolls_back(self):
        response = self.client.post(
            PERSISTENT_AGENT_BASE_URL,
            data=json.dumps({
                'name': 'API Persistent Agent',
                'charter': 'Automate product updates',
                'schedule': '0 9 * * 1',
                'preferred_contact_endpoint': 'sms',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(PersistentAgent.objects.count(), 0)
        self.assertEqual(BrowserUseAgent.objects.count(), 0)

    def test_update_agent_fields(self):
        payload = self._create_agent_via_api()
        agent_id = payload['id']

        update_response = self.client.patch(
            f'{PERSISTENT_AGENT_BASE_URL}{agent_id}/',
            data=json.dumps({'charter': 'Refine outreach list', 'is_active': False}),
            content_type='application/json',
        )
        self.assertEqual(update_response.status_code, 200, update_response.content)

        agent = PersistentAgent.objects.get(id=agent_id)
        self.assertEqual(agent.charter, 'Refine outreach list')
        self.assertFalse(agent.is_active)

    def test_update_agent_preferred_endpoint_to_sms(self):
        payload = self._create_agent_via_api({'preferred_contact_endpoint': 'email'})
        agent_id = payload['id']
        UserPhoneNumber.objects.create(
            user=self.user,
            phone_number='+15550000002',
            is_primary=True,
            is_verified=True,
        )

        update_response = self.client.patch(
            f'{PERSISTENT_AGENT_BASE_URL}{agent_id}/',
            data=json.dumps({'preferred_contact_endpoint': 'sms'}),
            content_type='application/json',
        )
        self.assertEqual(update_response.status_code, 200, update_response.content)

        agent = PersistentAgent.objects.get(id=agent_id)
        self.assertEqual(agent.preferred_contact_endpoint.channel, CommsChannel.SMS)
        self.assertEqual(agent.preferred_contact_endpoint.address, '+15550000002')

    def test_soft_delete_agent_marks_expired(self):
        payload = self._create_agent_via_api({'schedule': '0 12 * * *'})
        agent_id = payload['id']

        delete_response = self.client.delete(f'{PERSISTENT_AGENT_BASE_URL}{agent_id}/')
        self.assertEqual(delete_response.status_code, 204, delete_response.content)

        agent = PersistentAgent.objects.get(id=agent_id)
        self.assertEqual(agent.life_state, PersistentAgent.LifeState.EXPIRED)
        self.assertFalse(agent.is_active)
        self.assertIsNone(agent.schedule)

    def test_message_submission_populates_timeline(self):
        payload = self._create_agent_via_api({'name': 'Timeline Agent'})
        agent = PersistentAgent.objects.get(id=payload['id'])

        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
            address='timeline-agent@example.com',
            is_primary=True,
        )

        message_body = {
            'channel': 'email',
            'sender': self.user.email,
            'recipient': 'timeline-agent@example.com',
            'body': 'Status update from API client.',
        }
        message_response = self.client.post(
            f"{PERSISTENT_AGENT_BASE_URL}{agent.id}/messages/",
            data=json.dumps(message_body),
            content_type='application/json',
        )
        self.assertEqual(message_response.status_code, 201, message_response.content)
        event = message_response.json().get('event', {})
        self.assertEqual(event.get('kind'), 'message')

        timeline_response = self.client.get(f"{PERSISTENT_AGENT_BASE_URL}{agent.id}/timeline/")
        self.assertEqual(timeline_response.status_code, 200, timeline_response.content)
        events = timeline_response.json().get('events', [])
        self.assertTrue(any(evt.get('kind') == 'message' for evt in events))

    def test_processing_status_and_web_tasks(self):
        payload = self._create_agent_via_api()
        agent = PersistentAgent.objects.get(id=payload['id'])

        BrowserUseAgentTask.objects.create(
            agent=agent.browser_use_agent,
            user=self.user,
            prompt='Visit dashboard and summarize',
            status=BrowserUseAgentTask.StatusChoices.PENDING,
        )

        status_response = self.client.get(f"{PERSISTENT_AGENT_BASE_URL}{agent.id}/processing-status/")
        self.assertEqual(status_response.status_code, 200, status_response.content)
        status_payload = status_response.json()
        self.assertIn('processing_active', status_payload)
        self.assertIn('processing_snapshot', status_payload)

        tasks_response = self.client.get(f"{PERSISTENT_AGENT_BASE_URL}{agent.id}/web-tasks/?limit=10")
        self.assertEqual(tasks_response.status_code, 200, tasks_response.content)
        results = tasks_response.json().get('results', [])
        self.assertGreaterEqual(len(results), 1)
