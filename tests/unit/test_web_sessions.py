from __future__ import annotations

from datetime import timedelta
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentWebSession
from api.services.web_sessions import (
    end_web_session,
    get_deliverable_web_session,
    get_active_web_session,
    has_deliverable_web_session,
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
        first_visible = session.last_visible_at

        refreshed = heartbeat_web_session(session.session_key, self.agent, self.user)
        self.assertGreater(refreshed.session.last_seen_at, first_seen)
        self.assertGreater(refreshed.session.last_visible_at, first_visible)
        self.assertTrue(refreshed.session.is_visible)
        self.assertIsNone(refreshed.session.ended_at)

    @tag("batch_agent_chat")
    def test_hidden_heartbeat_marks_session_non_visible_but_keeps_it_live(self):
        result = start_web_session(self.agent, self.user)
        session = result.session
        first_visible = session.last_visible_at

        refreshed = heartbeat_web_session(
            session.session_key,
            self.agent,
            self.user,
            is_visible=False,
        )
        self.assertFalse(refreshed.session.is_visible)
        self.assertEqual(refreshed.session.last_visible_at, first_visible)
        self.assertIsNotNone(get_active_web_session(self.agent, self.user))

    @tag("batch_agent_chat")
    def test_deliverable_session_respects_visibility_grace(self):
        result = start_web_session(self.agent, self.user)
        session = result.session
        now = timezone.now()

        PersistentAgentWebSession.objects.filter(pk=session.pk).update(
            is_visible=False,
            last_seen_at=now - timedelta(seconds=30),
            last_visible_at=now - timedelta(seconds=30),
        )
        self.assertIsNotNone(get_deliverable_web_session(self.agent, self.user))
        self.assertTrue(has_deliverable_web_session(self.agent))

        PersistentAgentWebSession.objects.filter(pk=session.pk).update(
            is_visible=False,
            last_seen_at=now - timedelta(seconds=30),
            last_visible_at=now - timedelta(seconds=61),
        )
        self.assertIsNone(get_deliverable_web_session(self.agent, self.user))
        self.assertFalse(has_deliverable_web_session(self.agent))

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

        ended = PersistentAgentWebSession.objects.get(session_key=session_key)
        self.assertIsNotNone(ended.ended_at)

    @tag("batch_agent_chat")
    def test_start_creates_distinct_session_per_tab(self):
        first = start_web_session(self.agent, self.user)

        second = start_web_session(self.agent, self.user)
        self.assertNotEqual(second.session.session_key, first.session.session_key)
        self.assertNotEqual(second.session.id, first.session.id)
        self.assertEqual(PersistentAgentWebSession.objects.filter(agent=self.agent, user=self.user).count(), 2)

    @tag("batch_agent_chat")
    def test_hidden_tab_does_not_mask_visible_tab_for_delivery(self):
        first = start_web_session(self.agent, self.user)
        second = start_web_session(self.agent, self.user)

        heartbeat_web_session(second.session.session_key, self.agent, self.user, is_visible=True)
        heartbeat_web_session(first.session.session_key, self.agent, self.user, is_visible=False)

        deliverable = get_deliverable_web_session(self.agent, self.user)
        self.assertIsNotNone(deliverable)
        self.assertEqual(deliverable.session_key, second.session.session_key)
        self.assertTrue(has_deliverable_web_session(self.agent))

    @tag("batch_agent_chat")
    def test_heartbeat_rejects_unknown_session_key(self):
        first = start_web_session(self.agent, self.user)
        original_key = first.session.session_key

        PersistentAgentWebSession.objects.filter(pk=first.session.pk).update(
            session_key=uuid.uuid4()
        )

        with self.assertRaises(ValueError):
            heartbeat_web_session(original_key, self.agent, self.user)
