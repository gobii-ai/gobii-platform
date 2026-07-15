from types import SimpleNamespace

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

from api.agent.core import contact_snapshot as contact_snapshot_module
from api.agent.core import prompt_context as prompt_context_module
from api.agent.core.prompt_run_cache import (
    CONTACTS_SNAPSHOT,
    FILES_SNAPSHOT,
    MESSAGES_SNAPSHOT,
    PromptRunCache,
    bind_prompt_run_cache,
    reset_prompt_run_cache,
)
from api.agent.prompt_cache_signals import (
    invalidate_agent_contacts_prompt_snapshot,
    invalidate_agent_files_prompt_snapshot,
    invalidate_endpoint_prompt_snapshots,
    invalidate_files_prompt_snapshot,
    invalidate_message_cc_prompt_snapshot,
    invalidate_message_prompt_snapshots,
    invalidate_message_related_prompt_snapshot,
)
from api.agent.core.prompt_context import build_prompt_context
from api.agent.tools.sqlite_state import agent_sqlite_db
from api.models import BrowserUseAgent, PersistentAgent


@tag("batch_event_processing")
class PromptRunCacheTests(SimpleTestCase):
    def test_snapshots_reuse_until_their_domain_is_invalidated(self):
        cache = PromptRunCache(agent_id="agent-1")
        builds = {MESSAGES_SNAPSHOT: 0, CONTACTS_SNAPSHOT: 0, FILES_SNAPSHOT: 0}

        def build(domain):
            builds[domain] += 1
            return f"{domain}-{builds[domain]}"

        first, first_hit = cache.get_or_build(
            MESSAGES_SNAPSHOT,
            lambda: build(MESSAGES_SNAPSHOT),
        )
        second, second_hit = cache.get_or_build(
            MESSAGES_SNAPSHOT,
            lambda: build(MESSAGES_SNAPSHOT),
        )
        cache.invalidate(MESSAGES_SNAPSHOT)
        third, third_hit = cache.get_or_build(
            MESSAGES_SNAPSHOT,
            lambda: build(MESSAGES_SNAPSHOT),
        )

        self.assertEqual(first, second)
        self.assertNotEqual(second, third)
        self.assertFalse(first_hit)
        self.assertTrue(second_hit)
        self.assertFalse(third_hit)
        self.assertEqual(builds[MESSAGES_SNAPSHOT], 2)

    def test_human_generation_invalidates_messages_and_contacts_only(self):
        cache = PromptRunCache(agent_id="agent-1")
        for domain in (MESSAGES_SNAPSHOT, CONTACTS_SNAPSHOT, FILES_SNAPSHOT):
            cache.get_or_build(domain, lambda domain=domain: domain)

        cache.observe_human_generation(1)
        cache.observe_human_generation(2)

        self.assertTrue(cache.get_or_build(FILES_SNAPSHOT, lambda: "new-files")[1])
        self.assertFalse(cache.get_or_build(MESSAGES_SNAPSHOT, lambda: "new-messages")[1])
        self.assertFalse(cache.get_or_build(CONTACTS_SNAPSHOT, lambda: "new-contacts")[1])

    def test_disabled_snapshot_reuse_always_rebuilds(self):
        cache = PromptRunCache(agent_id="agent-1", snapshot_reuse_enabled=False)
        builds = []

        cache.get_or_build(MESSAGES_SNAPSHOT, lambda: builds.append(1) or "first")
        value, hit = cache.get_or_build(MESSAGES_SNAPSHOT, lambda: builds.append(2) or "second")

        self.assertEqual(value, "second")
        self.assertFalse(hit)
        self.assertEqual(builds, [1, 2])

    def test_signal_invalidation_is_scoped_to_active_agent_and_domain(self):
        cache = PromptRunCache(agent_id="agent-1")
        for domain in (MESSAGES_SNAPSHOT, CONTACTS_SNAPSHOT, FILES_SNAPSHOT):
            cache.get_or_build(domain, lambda domain=domain: domain)
        token = bind_prompt_run_cache(cache)
        try:
            invalidate_message_prompt_snapshots(
                None,
                SimpleNamespace(owner_agent_id="other-agent"),
            )
            self.assertTrue(
                cache.get_or_build(MESSAGES_SNAPSHOT, lambda: "unexpected")[1]
            )

            invalidate_message_prompt_snapshots(
                None,
                SimpleNamespace(owner_agent_id="agent-1"),
            )
            self.assertFalse(cache.get_or_build(MESSAGES_SNAPSHOT, lambda: "messages-2")[1])
            self.assertFalse(cache.get_or_build(CONTACTS_SNAPSHOT, lambda: "contacts-2")[1])

            invalidate_files_prompt_snapshot(None, SimpleNamespace())
            self.assertFalse(cache.get_or_build(FILES_SNAPSHOT, lambda: "files-2")[1])

            invalidate_agent_contacts_prompt_snapshot(
                None,
                SimpleNamespace(agent_id="agent-1"),
            )
            self.assertFalse(cache.get_or_build(CONTACTS_SNAPSHOT, lambda: "contacts-3")[1])
        finally:
            reset_prompt_run_cache(token)

        other_cache = PromptRunCache(agent_id="agent-2")
        value, hit = other_cache.get_or_build(MESSAGES_SNAPSHOT, lambda: "agent-2")
        self.assertEqual(value, "agent-2")
        self.assertFalse(hit)

    def test_mutation_signals_invalidate_only_required_domains(self):
        cache = PromptRunCache(agent_id="agent-1")
        token = bind_prompt_run_cache(cache)

        def prime():
            for domain in (MESSAGES_SNAPSHOT, CONTACTS_SNAPSHOT, FILES_SNAPSHOT):
                cache.get_or_build(domain, lambda domain=domain: object())

        def cache_hits():
            return {
                domain: cache.get_or_build(domain, lambda: object())[1]
                for domain in (MESSAGES_SNAPSHOT, CONTACTS_SNAPSHOT, FILES_SNAPSHOT)
            }

        try:
            prime()
            message = SimpleNamespace(owner_agent_id="agent-1")
            invalidate_message_related_prompt_snapshot(
                None,
                SimpleNamespace(message=message),
            )
            self.assertEqual(
                cache_hits(),
                {MESSAGES_SNAPSHOT: False, CONTACTS_SNAPSHOT: True, FILES_SNAPSHOT: True},
            )

            invalidate_message_related_prompt_snapshot(None, SimpleNamespace(message=message))
            self.assertEqual(
                cache_hits(),
                {MESSAGES_SNAPSHOT: False, CONTACTS_SNAPSHOT: True, FILES_SNAPSHOT: True},
            )

            invalidate_endpoint_prompt_snapshots(None, SimpleNamespace())
            self.assertEqual(
                cache_hits(),
                {MESSAGES_SNAPSHOT: False, CONTACTS_SNAPSHOT: True, FILES_SNAPSHOT: True},
            )

            invalidate_agent_contacts_prompt_snapshot(
                None,
                SimpleNamespace(agent_id="agent-1"),
            )
            self.assertEqual(
                cache_hits(),
                {MESSAGES_SNAPSHOT: True, CONTACTS_SNAPSHOT: False, FILES_SNAPSHOT: True},
            )

            invalidate_message_cc_prompt_snapshot(
                None,
                SimpleNamespace(owner_agent_id="agent-1"),
            )
            self.assertEqual(
                cache_hits(),
                {MESSAGES_SNAPSHOT: True, CONTACTS_SNAPSHOT: False, FILES_SNAPSHOT: True},
            )

            invalidate_agent_files_prompt_snapshot(
                None,
                SimpleNamespace(agent_id="agent-1"),
            )
            self.assertEqual(
                cache_hits(),
                {MESSAGES_SNAPSHOT: True, CONTACTS_SNAPSHOT: True, FILES_SNAPSHOT: False},
            )

            invalidate_files_prompt_snapshot(None, SimpleNamespace())
            self.assertEqual(
                cache_hits(),
                {MESSAGES_SNAPSHOT: True, CONTACTS_SNAPSHOT: True, FILES_SNAPSHOT: False},
            )
        finally:
            reset_prompt_run_cache(token)


@tag("batch_event_processing")
class PromptRunCacheIntegrationTests(TestCase):
    def test_repeated_prompt_render_reuses_expensive_snapshots(self):
        user = get_user_model().objects.create_user(
            username="prompt-cache-owner",
            email="prompt-cache-owner@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Prompt Cache Browser")
        agent = PersistentAgent.objects.create(
            user=user,
            name="Prompt Cache Agent",
            charter="Test prompt cache",
            browser_use_agent=browser_agent,
        )
        cache = PromptRunCache(agent_id=str(agent.id))

        with (
            agent_sqlite_db(str(agent.id)),
            patch("api.agent.core.prompt_context._archive_prompt_render", return_value=None),
            patch(
                "api.agent.core.prompt_context._build_sqlite_messages_snapshot_records",
                wraps=prompt_context_module._build_sqlite_messages_snapshot_records,
            ) as build_messages,
            patch(
                "api.agent.core.prompt_context._build_sqlite_files_snapshot",
                wraps=prompt_context_module._build_sqlite_files_snapshot,
            ) as build_files,
            patch(
                "api.agent.core.contact_snapshot.build_contact_activity_by_key",
                wraps=contact_snapshot_module.build_contact_activity_by_key,
            ) as build_contact_activity,
        ):
            build_prompt_context(agent, run_cache=cache, routing_token_seed=0)
            build_prompt_context(agent, run_cache=cache, routing_token_seed=0)

        self.assertEqual(build_messages.call_count, 1)
        self.assertEqual(build_files.call_count, 1)
        self.assertEqual(build_contact_activity.call_count, 1)
