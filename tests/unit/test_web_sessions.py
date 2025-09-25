from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone
from django.urls import reverse

from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentWebSession
from api.services.web_sessions import (
    WEB_SESSION_TTL_SECONDS,
    end_web_session,
    get_active_web_session,
    heartbeat_web_session,
    start_web_session,
)


@tag("batch_web_sessions")
class WebSessionServiceTests(TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.owner,
            name="Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Helper",
            charter="Assist the team",
            browser_use_agent=self.browser_agent,
        )

    def test_start_and_heartbeat_session(self):
        result = start_web_session(self.agent, self.owner)
        session = result.session
        self.assertIsNotNone(session.session_key)

        previous_seen = session.last_seen_at
        heartbeat_web_session(
            session_key=session.session_key,
            agent=self.agent,
            user=self.owner,
        )
        session.refresh_from_db()
        self.assertGreater(session.last_seen_at, previous_seen)

    def test_get_active_session_respects_ttl(self):
        result = start_web_session(self.agent, self.owner)
        session = result.session

        PersistentAgentWebSession.objects.filter(pk=session.pk).update(
            last_seen_at=timezone.now() - timedelta(seconds=WEB_SESSION_TTL_SECONDS + 5)
        )

        active = get_active_web_session(self.agent, self.owner)
        self.assertIsNone(active)

        session.refresh_from_db()
        self.assertIsNotNone(session.ended_at)

    def test_end_session_marks_ended(self):
        result = start_web_session(self.agent, self.owner)
        session = result.session

        end_web_session(
            session_key=session.session_key,
            agent=self.agent,
            user=self.owner,
        )

        session.refresh_from_db()
        self.assertIsNotNone(session.ended_at)

    def test_session_view_start_heartbeat_and_end(self):
        self.client.force_login(self.owner)
        url = reverse('agent_web_session', args=[self.agent.id])

        start_response = self.client.post(
            url,
            data=json.dumps({"action": "start"}),
            content_type='application/json',
        )
        self.assertEqual(start_response.status_code, 201)
        session_id = start_response.json().get('session_id')
        self.assertTrue(session_id)

        heartbeat_response = self.client.post(
            url,
            data=json.dumps({"action": "heartbeat", "session_id": session_id}),
            content_type='application/json',
        )
        self.assertEqual(heartbeat_response.status_code, 200)

        end_response = self.client.post(
            url,
            data=json.dumps({"action": "end", "session_id": session_id}),
            content_type='application/json',
        )
        self.assertEqual(end_response.status_code, 200)

    def test_session_view_rejects_invalid_action(self):
        self.client.force_login(self.owner)
        url = reverse('agent_web_session', args=[self.agent.id])

        response = self.client.post(
            url,
            data=json.dumps({"action": "unknown"}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
