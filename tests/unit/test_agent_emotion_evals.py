import json
from datetime import timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase, tag
from django.utils import timezone

import api.evals.loader  # noqa: F401 - registers canonical scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.agent_emotions import (
    AGENT_TEMPORARY_EMOTION_LIFECYCLE,
    EMOTION_TURNS,
    INITIAL_CHARTER,
    ORDINARY_WORK_PROMPT,
    ORDINARY_WORK_TASK,
    brief_reply_failures,
    emotion_state_failures,
    emotion_trace_failures,
    ordinary_work_failures,
)
from api.evals.suites import SuiteRegistry


def _sqlite_call(sql, *, status="complete", result_status="ok"):
    return SimpleNamespace(
        tool_name="sqlite_batch",
        tool_params={"sql": sql},
        status=status,
        result=json.dumps({"status": result_status}),
    )


@tag("batch_eval_fingerprint")
class AgentEmotionEvalTests(SimpleTestCase):
    def test_scenario_registers_without_a_single_scenario_suite(self):
        scenario = ScenarioRegistry.get(AGENT_TEMPORARY_EMOTION_LIFECYCLE)

        self.assertIsNotNone(scenario)
        self.assertIsNone(SuiteRegistry.get(AGENT_TEMPORARY_EMOTION_LIFECYCLE))
        self.assertIn(AGENT_TEMPORARY_EMOTION_LIFECYCLE, SuiteRegistry.get("core").scenario_slugs)
        self.assertIn(AGENT_TEMPORARY_EMOTION_LIFECYCLE, SuiteRegistry.get("all").scenario_slugs)
        self.assertEqual(
            [task.name for task in scenario.tasks],
            [
                "verify_initial_bounded_emotion",
                "verify_emotion_update",
                "verify_emotion_clear",
                ORDINARY_WORK_TASK,
                "verify_no_durable_config_leak",
            ],
        )
        self.assertIn("multi_turn", scenario.tags)
        self.assertIn("sqlite", scenario.tags)

    def test_prompts_are_natural_and_do_not_leak_the_control_plane(self):
        prompts = [turn.prompt for turn in EMOTION_TURNS] + [ORDINARY_WORK_PROMPT]
        for value in prompts:
            prompt = value.casefold()
            for forbidden in (
                "sqlite",
                "__agent_config",
                "charter",
                "emotion_timeout_seconds",
                "database",
            ):
                self.assertNotIn(forbidden, prompt)

    def test_trace_scorer_accepts_equivalent_bounded_set_and_clear_sql(self):
        set_call = _sqlite_call(
            'UPDATE "__agent_config" '
            "SET emotion = '🔥', emotion_timeout_seconds = 60 * 120 WHERE id = 1; "
            "SELECT emotion FROM __agent_config WHERE id = 1"
        )
        clear_call = _sqlite_call(
            "UPDATE [__agent_config] SET emotion = NULL, emotion_timeout_seconds = NULL WHERE id = 1"
        )

        self.assertEqual(emotion_trace_failures([set_call]), [])
        self.assertEqual(emotion_trace_failures([clear_call]), [])

    def test_trace_scorer_rejects_partial_failed_retried_and_durable_mutations(self):
        partial = _sqlite_call(
            "UPDATE __agent_config SET emotion = '🔥' WHERE id = 1"
        )
        failed = _sqlite_call(
            "UPDATE __agent_config SET emotion = '🔥', emotion_timeout_seconds = 7200 WHERE id = 1",
            status="error",
            result_status="error",
        )
        durable = _sqlite_call(
            "UPDATE __agent_config SET emotion = '🔥', emotion_timeout_seconds = 7200, "
            "charter = 'Stay fiery' WHERE id = 1; "
            "DELETE FROM __agent_schedules WHERE schedule_key = 'digest'"
        )
        retry = _sqlite_call("SELECT emotion FROM __agent_config WHERE id = 1")

        self.assertIn(
            "emotion and timeout were not changed together",
            emotion_trace_failures([partial]),
        )
        self.assertIn(
            "emotion config mutation did not complete successfully",
            emotion_trace_failures([failed]),
        )
        durable_failures = emotion_trace_failures([durable])
        self.assertIn("temporary emotion mutation also changed durable config", durable_failures)
        self.assertIn("temporary emotion mutation also changed schedule rows", durable_failures)
        self.assertIn(
            "expected one SQLite call, found 2",
            emotion_trace_failures([retry, failed]),
        )

    def test_state_scorer_accepts_requested_expiry_update_and_clear(self):
        now = timezone.now().replace(microsecond=0)
        first_expiry = now + timedelta(hours=2)
        first_agent = SimpleNamespace(
            charter=INITIAL_CHARTER,
            emotion="🔥",
            emotion_expires_at=first_expiry,
        )
        update_expiry = now + timedelta(minutes=30)
        updated_agent = SimpleNamespace(
            charter=INITIAL_CHARTER,
            emotion="😌",
            emotion_expires_at=update_expiry,
        )
        cleared_agent = SimpleNamespace(
            charter=INITIAL_CHARTER,
            emotion="",
            emotion_expires_at=None,
        )
        inbound = SimpleNamespace(timestamp=now)

        self.assertEqual(emotion_state_failures(EMOTION_TURNS[0], first_agent, inbound), [])
        self.assertEqual(
            emotion_state_failures(
                EMOTION_TURNS[1],
                updated_agent,
                inbound,
                previous_expiry=first_expiry,
            ),
            [],
        )
        self.assertEqual(emotion_state_failures(EMOTION_TURNS[2], cleared_agent, inbound), [])
        cleared_agent.emotion = None
        self.assertIn(
            "clearing the emotion did not clear both persisted fields",
            emotion_state_failures(EMOTION_TURNS[2], cleared_agent, inbound),
        )

    def test_state_scorer_rejects_unbounded_or_stacked_expiry(self):
        now = timezone.now().replace(microsecond=0)
        inbound = SimpleNamespace(timestamp=now)
        unbounded = SimpleNamespace(
            emotion="🔥",
            emotion_expires_at=now + timedelta(hours=25),
        )
        old_expiry = now + timedelta(hours=2)
        stacked = SimpleNamespace(
            emotion="😌",
            emotion_expires_at=old_expiry + timedelta(minutes=30),
        )

        self.assertIn(
            "temporary emotion expiry was not bounded to 24 hours",
            emotion_state_failures(EMOTION_TURNS[0], unbounded, inbound),
        )
        self.assertIn(
            "emotion update stacked onto the old expiry instead of replacing it",
            emotion_state_failures(
                EMOTION_TURNS[1],
                stacked,
                inbound,
                previous_expiry=old_expiry,
            ),
        )

    def test_reply_scorer_requires_one_brief_natural_reply(self):
        good = [SimpleNamespace(body="Done. 😌 for the next 30 minutes.")]
        internal = [SimpleNamespace(body="I updated __agent_config in SQLite. Anything else?")]

        self.assertEqual(brief_reply_failures(good), [])
        failures = brief_reply_failures(internal)
        self.assertIn("reply exposed implementation details", failures)

    def test_ordinary_work_accepts_direct_or_sqlite_logic_and_rejects_state_mutation(self):
        agent = SimpleNamespace(
            emotion="",
            emotion_expires_at=None,
            charter=INITIAL_CHARTER,
            schedule=None,
        )
        answer = [SimpleNamespace(body="136.")]
        logic_read = _sqlite_call("SELECT 17 * 8 AS answer")

        self.assertEqual(
            ordinary_work_failures(agent, [], answer, expected_schedule=None),
            [],
        )
        self.assertEqual(
            ordinary_work_failures(agent, [logic_read], answer, expected_schedule=None),
            [],
        )

        config_write = _sqlite_call(
            "UPDATE __agent_config SET emotion = '🔥', emotion_timeout_seconds = 60 WHERE id = 1"
        )
        schedule_write = _sqlite_call(
            "DELETE FROM __agent_schedules WHERE schedule_key = 'daily'"
        )
        self.assertIn(
            "ordinary work mutated agent config",
            ordinary_work_failures(agent, [config_write], answer, expected_schedule=None),
        )
        self.assertIn(
            "ordinary work mutated schedule rows",
            ordinary_work_failures(agent, [schedule_write], answer, expected_schedule=None),
        )

        agent.emotion = "🔥"
        agent.emotion_expires_at = timezone.now() + timedelta(minutes=1)
        self.assertIn(
            "ordinary work recreated a cleared emotion",
            ordinary_work_failures(agent, [], answer, expected_schedule=None),
        )
        self.assertIn(
            "ordinary-work reply did not answer 17 × 8 correctly",
            ordinary_work_failures(
                agent,
                [],
                [SimpleNamespace(body="135.")],
                expected_schedule=None,
            ),
        )
