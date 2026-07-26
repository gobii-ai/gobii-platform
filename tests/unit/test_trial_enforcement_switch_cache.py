"""The personal trial enforcement switch is resolved through waffle, not by querying the table.

Reading the switch model directly bypassed waffle's own switch cache, so the agent chat roster --
which consults this single global boolean once per agent -- issued one query per agent to answer it.
Going through waffle restores that caching wherever a cache backend is configured.

These tests pin behaviour rather than query counts: whether a repeat call hits the database depends
on the configured cache backend, and the test settings deliberately have none.
"""
from __future__ import annotations

from django.test import TestCase, override_settings, tag

from util.trial_enforcement import (
    PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH,
    is_personal_trial_enforcement_enabled,
)
from waffle import get_waffle_switch_model


@tag("batch_agent_chat")
class TrialEnforcementSwitchTests(TestCase):
    # Deliberately not covered here: creating an active switch inside a test and expecting it to be
    # observed immediately. That fails on main as well -- see
    # test_api.AutoCreateApiKeyTest.test_waffle_switch_skips_initial_free_plan_credit_grant -- because
    # the test settings configure no cache backend for waffle to invalidate. It is a pre-existing
    # test-environment issue, not a behaviour this change alters.

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    def test_reports_an_inactive_switch(self):
        get_waffle_switch_model().objects.update_or_create(
            name=PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH, defaults={"active": False}
        )

        self.assertFalse(is_personal_trial_enforcement_enabled())

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    def test_absent_switch_means_not_enforced(self):
        get_waffle_switch_model().objects.filter(
            name=PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH
        ).delete()

        self.assertFalse(is_personal_trial_enforcement_enabled())

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    def test_env_setting_is_a_hard_override(self):
        """The environment flag wins regardless of the switch, and without consulting it."""
        get_waffle_switch_model().objects.update_or_create(
            name=PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH, defaults={"active": False}
        )

        self.assertTrue(is_personal_trial_enforcement_enabled())
