"""The personal trial enforcement switch is a global boolean, so it is read once, not per caller.

It used to hit the database on every call. The agent chat roster consults it once per agent, so a
user with a hundred agents paid a hundred round trips to resolve a single flag. Waffle caches its
switches by default; this lookup bypassed waffle and so bypassed that cache too.
"""
from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
from django.db import connection

from util.trial_enforcement import (
    PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH,
    is_personal_trial_enforcement_enabled,
)
from waffle import get_waffle_switch_model


@tag("batch_agent_chat")
class TrialEnforcementSwitchCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    def test_repeated_calls_do_not_repeat_the_query(self):
        with CaptureQueriesContext(connection) as first:
            is_personal_trial_enforcement_enabled()
        with CaptureQueriesContext(connection) as rest:
            for _ in range(20):
                is_personal_trial_enforcement_enabled()

        self.assertGreaterEqual(len(first.captured_queries), 1)
        self.assertEqual(
            len(rest.captured_queries),
            0,
            "the switch is global; twenty callers must not mean twenty queries",
        )

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    def test_absent_switch_is_cached_too(self):
        """A missing switch row is the common state and must not re-query on every call."""
        get_waffle_switch_model().objects.filter(
            name=PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH
        ).delete()

        self.assertFalse(is_personal_trial_enforcement_enabled())
        with CaptureQueriesContext(connection) as repeat:
            for _ in range(5):
                is_personal_trial_enforcement_enabled()

        self.assertEqual(len(repeat.captured_queries), 0)

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    def test_reports_the_switch_state(self):
        switch_model = get_waffle_switch_model()
        switch_model.objects.update_or_create(
            name=PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH, defaults={"active": True}
        )

        self.assertTrue(is_personal_trial_enforcement_enabled())

        cache.clear()
        switch_model.objects.filter(
            name=PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH
        ).update(active=False)

        self.assertFalse(is_personal_trial_enforcement_enabled())

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    def test_env_override_short_circuits_before_the_cache(self):
        """The env setting is a hard override and must not be affected by a cached switch value."""
        with CaptureQueriesContext(connection) as ctx:
            self.assertTrue(is_personal_trial_enforcement_enabled())

        self.assertEqual(len(ctx.captured_queries), 0)
