"""Personal-agent access is resolved once per user, not once per agent.

The answer depends only on the user, but the roster asks it while building every agent's payload, and
resolving it consults a global waffle switch. A user with a hundred agents paid a hundred queries to
answer one question. The result is memoized on the user instance, which lives exactly as long as the
request -- so there is no shared cache and nothing to invalidate between requests.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
from django.db import connection

from console.agent_chat.access import _can_access_personal_agent


@tag("batch_agent_chat")
class PersonalAgentAccessMemoTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="access-memo@example.test", email="access-memo@example.test"
        )

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    def test_repeated_checks_for_one_user_resolve_once(self):
        _can_access_personal_agent(self.user, allow_delinquent_personal_chat=True)

        with CaptureQueriesContext(connection) as repeat:
            for _ in range(25):
                _can_access_personal_agent(self.user, allow_delinquent_personal_chat=True)

        self.assertEqual(
            len(repeat.captured_queries),
            0,
            "the answer depends on the user alone; twenty-five agents must not mean twenty-five lookups",
        )

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    def test_the_two_access_modes_are_memoized_separately(self):
        """The delinquent-chat variant asks a different question and must not reuse the other answer."""
        strict = _can_access_personal_agent(self.user, allow_delinquent_personal_chat=False)
        lenient = _can_access_personal_agent(self.user, allow_delinquent_personal_chat=True)

        self.assertEqual(strict, _can_access_personal_agent(self.user, allow_delinquent_personal_chat=False))
        self.assertEqual(lenient, _can_access_personal_agent(self.user, allow_delinquent_personal_chat=True))

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    def test_a_freshly_loaded_user_resolves_again(self):
        """The memo must not outlive the request, so a new instance re-reads current state."""
        _can_access_personal_agent(self.user, allow_delinquent_personal_chat=True)

        reloaded = get_user_model().objects.get(pk=self.user.pk)
        with CaptureQueriesContext(connection) as fresh:
            _can_access_personal_agent(reloaded, allow_delinquent_personal_chat=True)

        self.assertGreaterEqual(len(fresh.captured_queries), 1)
