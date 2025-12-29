from __future__ import annotations

from datetime import timedelta
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentWebSession
from api.services.web_sessions import (
    end_web_session,
    get_active_web_session,
    heartbeat_web_session,
    start_web_session,
)


class WebSessionServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="session-owner",
            email="session-owner@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Session Browser Agent")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Session Tester",
            charter="Test web session lifecycle",
            browser_use_agent=cls.browser_agent,
        )

    @tag("batch_agent_chat")
    def test_start_and_heartbeat_refreshes_last_seen(self):
        result = start_web_session(self.agent, self.user)
        session = result.session
        first_seen = session.last_seen_at

        refreshed = heartbeat_web_session(session.session_key, self.agent, self.user)
        self.assertGreater(refreshed.session.last_seen_at, first_seen)
        self.assertIsNone(refreshed.session.ended_at)

    @tag("batch_agent_chat")
    def test_expired_session_is_marked_and_unavailable(self):
        result = start_web_session(self.agent, self.user)
        session = result.session
        # Simulate expiry by rewinding last_seen beyond the provided TTL.
        PersistentAgentWebSession.objects.filter(pk=session.pk).update(
            last_seen_at=timezone.now() - timedelta(seconds=20)
        )

        with self.assertRaises(ValueError):
            heartbeat_web_session(session.session_key, self.agent, self.user, ttl_seconds=5)

        self.assertIsNone(get_active_web_session(self.agent, self.user, ttl_seconds=5))

    @tag("batch_agent_chat")
    def test_end_session_marks_record(self):
        result = start_web_session(self.agent, self.user)
        session_key = result.session.session_key
        end_web_session(session_key, self.agent, self.user)

        ended = PersistentAgentWebSession.objects.get(agent=self.agent, user=self.user)
        self.assertIsNotNone(ended.ended_at)

    @tag("batch_agent_chat")
    def test_start_reuses_active_session(self):
        first = start_web_session(self.agent, self.user)
        original_key = first.session.session_key
        original_started_at = first.session.started_at

        second = start_web_session(self.agent, self.user)
        self.assertEqual(second.session.session_key, original_key)
        self.assertEqual(second.session.started_at, original_started_at)
        self.assertGreaterEqual(second.session.last_seen_at, first.session.last_seen_at)

    @tag("batch_agent_chat")
    def test_heartbeat_recovers_when_session_key_rotates(self):
        first = start_web_session(self.agent, self.user)
        original_key = first.session.session_key

        refreshed_key = uuid.uuid4()
        PersistentAgentWebSession.objects.filter(agent=self.agent, user=self.user).update(
            session_key=refreshed_key
        )

        recovered = heartbeat_web_session(original_key, self.agent, self.user)
        self.assertEqual(recovered.session.session_key, refreshed_key)
