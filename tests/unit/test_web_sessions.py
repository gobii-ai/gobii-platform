from __future__ import annotations

import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone
from django.urls import reverse

from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentWebSession
from api.services import web_sessions
from api.services.web_sessions import (
    WEB_SESSION_TTL_SECONDS,
    end_web_session,
    get_active_web_session,
    heartbeat_web_session,
    start_web_session,
    delete_expired_sessions,
)
from api.tasks.maintenance_tasks import cleanup_expired_web_sessions


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

    def test_delete_expired_sessions_handles_ended_rows(self):
        result = start_web_session(self.agent, self.owner)
        session = result.session

        original_retention = web_sessions.WEB_SESSION_RETENTION_DAYS
        try:
            web_sessions.WEB_SESSION_RETENTION_DAYS = 1
            session.ended_at = timezone.now() - timedelta(days=2)
            session.save(update_fields=["ended_at"])

            removed = delete_expired_sessions(batch_size=10)
            self.assertEqual(removed, 1)
            self.assertEqual(PersistentAgentWebSession.objects.count(), 0)
        finally:
            web_sessions.WEB_SESSION_RETENTION_DAYS = original_retention

    def test_delete_expired_sessions_removes_stale_unended_rows(self):
        result = start_web_session(self.agent, self.owner)
        session = result.session

        original_grace = web_sessions.WEB_SESSION_STALE_GRACE_MINUTES
        try:
            web_sessions.WEB_SESSION_STALE_GRACE_MINUTES = 0
            past = timezone.now() - timedelta(hours=3)
            PersistentAgentWebSession.objects.filter(pk=session.pk).update(
                last_seen_at=past,
                ended_at=None,
            )

            removed = delete_expired_sessions(batch_size=10)
            self.assertEqual(removed, 1)
            self.assertEqual(PersistentAgentWebSession.objects.count(), 0)
        finally:
            web_sessions.WEB_SESSION_STALE_GRACE_MINUTES = original_grace

    def test_cleanup_task_calls_service(self):
        with mock.patch("api.tasks.maintenance_tasks.delete_expired_sessions", return_value=2) as patched:
            cleanup_expired_web_sessions()
            patched.assert_called_once()

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
