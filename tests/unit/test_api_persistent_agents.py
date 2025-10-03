from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag

from api.models import (
    BrowserUseAgentTask,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
)


@tag('batch_persistent_agents_api')
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
        self._delay_patcher.start()
        self.addCleanup(self._delay_patcher.stop)

    def _create_agent_via_api(self, payload: dict | None = None) -> dict:
        data = {
            'name': 'API Persistent Agent',
            'charter': 'Automate product updates',
            'schedule': '0 9 * * 1',
        }
        if payload:
            data.update(payload)

        response = self.client.post(
            '/api/v1/agents/persistent/',
            data=json.dumps(data),
            content_type='application/json',
        )
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

    def test_update_agent_fields(self):
        payload = self._create_agent_via_api()
        agent_id = payload['id']

        update_response = self.client.patch(
            f'/api/v1/agents/persistent/{agent_id}/',
            data=json.dumps({'charter': 'Refine outreach list', 'is_active': False}),
            content_type='application/json',
        )
        self.assertEqual(update_response.status_code, 200, update_response.content)

        agent = PersistentAgent.objects.get(id=agent_id)
        self.assertEqual(agent.charter, 'Refine outreach list')
        self.assertFalse(agent.is_active)

    def test_soft_delete_agent_marks_expired(self):
        payload = self._create_agent_via_api({'schedule': '0 12 * * *'})
        agent_id = payload['id']

        delete_response = self.client.delete(f'/api/v1/agents/persistent/{agent_id}/')
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
            f"/api/v1/agents/persistent/{agent.id}/messages/",
            data=json.dumps(message_body),
            content_type='application/json',
        )
        self.assertEqual(message_response.status_code, 201, message_response.content)
        event = message_response.json().get('event', {})
        self.assertEqual(event.get('kind'), 'message')

        timeline_response = self.client.get(f"/api/v1/agents/persistent/{agent.id}/timeline/")
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

        status_response = self.client.get(f"/api/v1/agents/persistent/{agent.id}/processing-status/")
        self.assertEqual(status_response.status_code, 200, status_response.content)
        status_payload = status_response.json()
        self.assertIn('processing_active', status_payload)
        self.assertIn('processing_snapshot', status_payload)

        tasks_response = self.client.get(f"/api/v1/agents/persistent/{agent.id}/web-tasks/?limit=10")
        self.assertEqual(tasks_response.status_code, 200, tasks_response.content)
        results = tasks_response.json().get('results', [])
        self.assertGreaterEqual(len(results), 1)
