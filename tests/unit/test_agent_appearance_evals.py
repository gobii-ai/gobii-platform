import json
from datetime import datetime, timezone
from types import SimpleNamespace

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers canonical scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.agent_appearance import (
    AGENT_APPEARANCE_SCENARIO_SLUGS,
    AGENT_APPEARANCE_SUITE_SLUG,
    DELEGATED_APPEARANCE,
    DELEGATED_APPEARANCE_PROMPT,
    GENERIC_APPEARANCE,
    OWNER_APPEARANCE_PROMPT,
    OWNER_DIRECTED_APPEARANCE,
    ORDINARY_WORK_PROMPT,
    STABLE_APPEARANCE,
    UNAUTHORIZED_APPEARANCE,
    UNAUTHORIZED_APPEARANCE_PROMPT,
    AppearanceState,
    appearance_trace_failures,
    config_preservation_failures,
    delegated_appearance_failures,
    no_mutation_failures,
    owner_appearance_failures,
    reply_failures,
)
from api.evals.stop_policy import sqlite_batch_mutates_agent_config_field
from api.evals.suites import SuiteRegistry


def _call(tool_name, *, sql="", status="complete", result=None, will_continue_work=None):
    params = {"sql": sql} if tool_name == "sqlite_batch" else {}
    if will_continue_work is not None:
        params["will_continue_work"] = will_continue_work
    return SimpleNamespace(
        tool_name=tool_name,
        tool_params=params,
        status=status,
        result=json.dumps(result or {"status": "ok"}),
    )


def _appearance_call(sql=None, *, updated_fields=None, errors=None):
    return _call(
        "sqlite_batch",
        sql=sql or (
            "UPDATE __agent_config SET appearance='A distinctive look with dark curls' "
            "WHERE id=1"
        ),
        result={
            "status": "ok",
            "agent_config_update": {
                "updated_fields": ["appearance"] if updated_fields is None else updated_fields,
                "unchanged_fields": [],
                "errors": {} if errors is None else errors,
            },
        },
    )


def _reply_call(*, body=None, skipped=False, will_continue_work=False):
    call = _call("send_chat_message", will_continue_work=will_continue_work)
    if body is not None:
        call.tool_params["body"] = body
    if skipped:
        call.result = json.dumps({"status": "ok", "skipped": True})
    return call


def _state(appearance, **overrides):
    values = {
        "appearance": appearance,
        "charter": "Research vendors.",
        "schedule": "0 8 * * 1",
        "emotion": "🧭",
        "emotion_expires_at": datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc),
        "schedules": (("weekly-review", "Weekly review", "30 14 * * 5"),),
    }
    values.update(overrides)
    return AppearanceState(**values)


@tag("batch_eval_fingerprint")
class AgentAppearanceEvalTests(SimpleTestCase):
    def test_scenarios_and_focused_suite_are_registered(self):
        suite = SuiteRegistry.get(AGENT_APPEARANCE_SUITE_SLUG)

        self.assertEqual(tuple(suite.scenario_slugs), AGENT_APPEARANCE_SCENARIO_SLUGS)
        for slug in AGENT_APPEARANCE_SCENARIO_SLUGS:
            scenario = ScenarioRegistry.get(slug)
            self.assertIsNotNone(scenario)
            self.assertIn("appearance", scenario.tags)
            self.assertIn(slug, SuiteRegistry.get("core").scenario_slugs)
            self.assertIn(slug, SuiteRegistry.get("all").scenario_slugs)

        self.assertIn("multi_turn", ScenarioRegistry.get(DELEGATED_APPEARANCE).tags)
        self.assertIn("authorization", ScenarioRegistry.get(UNAUTHORIZED_APPEARANCE).tags)
        self.assertEqual(
            [task.name for task in ScenarioRegistry.get(OWNER_DIRECTED_APPEARANCE).tasks],
            [
                "inject_owner_appearance",
                "verify_owner_appearance",
                "verify_owner_appearance_trace",
                "verify_owner_appearance_reply",
            ],
        )

    def test_prompts_are_natural_and_do_not_leak_the_control_plane(self):
        for value in (
            OWNER_APPEARANCE_PROMPT,
            DELEGATED_APPEARANCE_PROMPT,
            ORDINARY_WORK_PROMPT,
            UNAUTHORIZED_APPEARANCE_PROMPT,
        ):
            prompt = value.casefold()
            for forbidden in ("sqlite", "__agent_config", "visual_description", "database", "charter"):
                self.assertNotIn(forbidden, prompt)

    def test_stop_policy_recognizes_appearance_mutations(self):
        call = _appearance_call()
        scenario = ScenarioRegistry.get(OWNER_DIRECTED_APPEARANCE)

        self.assertTrue(sqlite_batch_mutates_agent_config_field(call, "appearance"))
        self.assertEqual(
            scenario._appearance_stop_policy()["stop_when_all_seen"][0],
            {
                "tool_name": "sqlite_batch",
                "agent_config_field": "appearance",
                "after_execution": True,
            },
        )

    def test_trace_accepts_one_focused_reconciled_update_and_terminal_reply(self):
        calls = [
            _appearance_call(
                'UPDATE "__agent_config" SET appearance = '
                "'A woman with shoulder-length black curls and green glasses' WHERE id = 1"
            ),
            _reply_call(),
        ]

        self.assertEqual(appearance_trace_failures(calls), [])

        read_then_update = [
            _call("sqlite_batch", sql="SELECT appearance FROM __agent_config WHERE id=1"),
            *calls,
        ]
        self.assertEqual(appearance_trace_failures(read_then_update), [])

        verified_update = [
            _call("sqlite_batch", sql="SELECT appearance FROM __agent_config WHERE id=1"),
            _reply_call(body="I’m choosing a look now.", skipped=True, will_continue_work=True),
            _appearance_call(),
            _call("sqlite_batch", sql="SELECT appearance FROM __agent_config WHERE id=1"),
            _reply_call(body="Done."),
        ]
        self.assertEqual(appearance_trace_failures(verified_update), [])

    def test_trace_rejects_replace_extra_fields_retries_and_missing_reconciliation(self):
        replace = _appearance_call(
            "REPLACE INTO __agent_config (id, appearance) VALUES (1, 'Silver hair')"
        )
        overbroad = _appearance_call(
            "UPDATE __agent_config SET appearance='Silver hair', charter='New job' WHERE id=1"
        )
        retry = _call("sqlite_batch", sql="SELECT appearance FROM __agent_config WHERE id=1")
        unreconciled = _appearance_call(updated_fields=[], errors={})

        self.assertIn(
            "appearance was not changed with one focused UPDATE",
            appearance_trace_failures([replace, _reply_call()]),
        )
        self.assertTrue(
            any(
                "assigned unrelated config fields" in failure
                for failure in appearance_trace_failures([overbroad, _reply_call()])
            )
        )
        self.assertIn(
            "expected one update and at most two state reads, found 4 SQLite calls",
            appearance_trace_failures([retry, retry, retry, _appearance_call(), _reply_call()]),
        )
        self.assertIn(
            "appearance mutation was not reconciled cleanly",
            appearance_trace_failures([unreconciled, _reply_call()]),
        )

    def test_trace_rejects_nonterminal_or_forbidden_work(self):
        continuing_reply = _call("send_chat_message", will_continue_work=True)
        create_image = _call("create_image")

        self.assertIn(
            "appearance reply was not terminal",
            appearance_trace_failures([_appearance_call(), continuing_reply]),
        )
        failures = appearance_trace_failures([_appearance_call(), create_image, _reply_call()])
        self.assertTrue(any("forbidden tools" in failure for failure in failures))

    def test_owner_state_scorer_requires_edits_and_preserves_identity_and_config(self):
        before = _state(STABLE_APPEARANCE)
        after = _state(
            "A woman in her early forties with warm brown skin, hazel eyes, a small silver nose stud, "
            "shoulder-length black curls, round green glasses, and a mustard cardigan."
        )

        self.assertEqual(owner_appearance_failures(before, after), [])

        lost_identity = _state(
            "A person with shoulder-length black curls, round green glasses, and a mustard cardigan."
        )
        failures = owner_appearance_failures(before, lost_identity)
        self.assertTrue(any("brown skin" in failure for failure in failures))
        self.assertTrue(any("hazel" in failure for failure in failures))

        contradictory = _state(
            f"{STABLE_APPEARANCE} She also has shoulder-length black curls, round green glasses, "
            "and a mustard cardigan."
        )
        self.assertTrue(
            any("retained replaced detail" in failure for failure in owner_appearance_failures(before, contradictory))
        )

        changed_schedule = _state(after.appearance, schedule="0 9 * * 1")
        self.assertIn(
            "appearance work changed schedule",
            config_preservation_failures(before, changed_schedule),
        )

    def test_delegated_state_scorer_accepts_specific_bounded_identity(self):
        before = _state(GENERIC_APPEARANCE)
        after = _state(
            "A thoughtful woman in her 40s with warm olive skin, expressive brown eyes, "
            "short dark curly hair, copper glasses, and a relaxed forest-green jacket."
        )

        self.assertEqual(delegated_appearance_failures(before, after), [])

        vague = _state("A distinctive professional.")
        self.assertTrue(delegated_appearance_failures(before, vague))
        linked = _state(
            "A thoughtful person with dark hair, brown eyes, a navy jacket, and avatar https://example.test/me.png"
        )
        self.assertIn(
            "delegated appearance stored an external image or URL",
            delegated_appearance_failures(before, linked),
        )
        refusal = _state("I will not define hair, eyes, skin, clothing style, glasses, or age; keep it generic.")
        self.assertIn(
            "delegated appearance stored refusal or placeholder language",
            delegated_appearance_failures(before, refusal),
        )

    def test_nonmutation_and_reply_scorers_cover_ordinary_and_unauthorized_turns(self):
        read_only = _call("sqlite_batch", sql="SELECT 19 * 7 AS answer")
        mutation = _appearance_call()
        ordinary_reply = [SimpleNamespace(body="133.")]
        refusal = [SimpleNamespace(body="Only my owner can change my appearance.")]

        self.assertEqual(no_mutation_failures([read_only, _reply_call()]), [])
        self.assertEqual(
            no_mutation_failures(
                [
                    _reply_call(body="Let me check.", skipped=True, will_continue_work=True),
                    _reply_call(body="Only my owner can change my appearance."),
                ]
            ),
            [],
        )
        self.assertIn("turn mutated agent configuration", no_mutation_failures([mutation]))
        self.assertEqual(reply_failures(ordinary_reply), [])
        self.assertEqual(reply_failures(refusal, unauthorized=True), [])
        self.assertEqual(
            reply_failures(
                [SimpleNamespace(body="I'm not able to change my appearance; an owner or admin needs to do that.")],
                unauthorized=True,
            ),
            [],
        )
        self.assertEqual(
            reply_failures(
                [
                    SimpleNamespace(
                        body=(
                            "I can only change my appearance when requested by someone authorized to configure "
                            "the agent, that's the account owner or an organization admin."
                        )
                    )
                ],
                unauthorized=True,
            ),
            [],
        )
        self.assertIn(
            "unauthorized reply did not explain the ownership boundary",
            reply_failures([SimpleNamespace(body="I can't do that.")], unauthorized=True),
        )
        self.assertIn(
            "unauthorized reply did not explain the ownership boundary",
            reply_failures(
                [SimpleNamespace(body="You're authorized and allowed to change my appearance whenever you'd like.")],
                unauthorized=True,
            ),
        )
        self.assertIn(
            "unauthorized reply did not explain the ownership boundary",
            reply_failures(
                [SimpleNamespace(body="I can only change when a friend asks; the owner isn't involved.")],
                unauthorized=True,
            ),
        )
        self.assertIn(
            "unauthorized reply did not explain the ownership boundary",
            reply_failures(
                [
                    SimpleNamespace(
                        body="I can only change my appearance when requested by anyone, not the owner."
                    )
                ],
                unauthorized=True,
            ),
        )
        self.assertEqual(reply_failures([SimpleNamespace(body="A" * 750)]), [])
        self.assertIn(
            "appearance reply was empty or needlessly long",
            reply_failures([SimpleNamespace(body="A" * 751)]),
        )
