"""A mood change has to reach the timeline as a mood, not as a row update.

Agents set their emotion by writing to their own config table, so by the time the step reached the
client it was an ordinary sqlite_batch and the card read "Database query, 1 statement" -- the same
as any SELECT beside it. Nothing in the payload said an emotion had changed or what it became, so
no renderer could have drawn anything better. The value travels on the entry now.
"""
from __future__ import annotations

from django.test import SimpleTestCase, tag

from api.agent.core.event_processing import _capture_tool_display_metadata


class _Prepared:
    def __init__(self, tool_name: str, exec_params: dict):
        self.tool_name = tool_name
        self.exec_params = exec_params


EMOTION_SQL = {
    "sql": "UPDATE __agent_config SET emotion = '\U0001F60A', emotion_timeout_seconds = 3600 WHERE id = 1;"
}


@tag("batch_agent_chat")
class EmotionDisplayMetadataTests(SimpleTestCase):
    """The capture step decides what the UI is even able to know."""

    def _capture(self, snapshot, params=None):
        from unittest.mock import patch

        with patch(
            "api.agent.core.event_processing.read_sqlite_agent_config_snapshot",
            return_value=snapshot,
        ):
            return _capture_tool_display_metadata(
                _Prepared("sqlite_batch", params if params is not None else EMOTION_SQL),
                {"status": "ok"},
            )

    def test_emotion_and_timeout_are_carried(self):
        from api.agent.tools.sqlite_agent_config import AgentConfigSnapshot

        snapshot = AgentConfigSnapshot(
            charter="c", schedule=None, emotion="\U0001F60A", emotion_timeout_seconds=3600
        )

        captured = self._capture(snapshot)["agent_config"]

        self.assertEqual(captured["emotion"], "\U0001F60A")
        self.assertEqual(captured["emotion_timeout_seconds"], 3600)

    def test_a_cleared_emotion_is_carried_as_none_rather_than_omitted(self):
        """Omitting it would be indistinguishable from "no emotion involved" downstream."""
        from api.agent.tools.sqlite_agent_config import AgentConfigSnapshot

        snapshot = AgentConfigSnapshot(charter="c", schedule=None, emotion=None)

        captured = self._capture(snapshot)["agent_config"]

        self.assertIn("emotion", captured)
        self.assertIsNone(captured["emotion"])

    def test_charter_and_schedule_still_travel(self):
        from api.agent.tools.sqlite_agent_config import AgentConfigSnapshot

        snapshot = AgentConfigSnapshot(charter="the charter", schedule="0 9 * * *", emotion="\U0001F60A")

        captured = self._capture(snapshot)["agent_config"]

        self.assertEqual(captured["charter"], "the charter")
        self.assertEqual(captured["schedule"], "0 9 * * *")

    def test_a_charter_only_write_carries_no_emotion(self):
        """A charter edit is not a mood event, even though the snapshot always has an emotion slot.

        Carrying `emotion: None` for a write that never touched the mood made every charter or
        schedule update by a mood-less agent render as "let their mood settle" (bug #462).
        """
        from api.agent.tools.sqlite_agent_config import AgentConfigSnapshot

        snapshot = AgentConfigSnapshot(charter="c", schedule=None, emotion=None)

        captured = self._capture(
            snapshot, {"sql": "UPDATE __agent_config SET charter = 'new charter' WHERE id = 1;"}
        )["agent_config"]

        self.assertNotIn("emotion", captured)
        self.assertNotIn("emotion_timeout_seconds", captured)

    def test_a_schedule_timer_write_carries_no_emotion(self):
        """Timer inserts hit the same capture path and were the other source of phantom cards."""
        from api.agent.tools.sqlite_agent_config import AgentConfigSnapshot

        snapshot = AgentConfigSnapshot(charter="c", schedule=None, emotion=None)

        captured = self._capture(
            snapshot,
            {"sql": "INSERT INTO __agent_schedules (name, cron) VALUES ('recheck', '0 9 * * *');"},
        )["agent_config"]

        self.assertNotIn("emotion", captured)


@tag("batch_agent_chat")
class EmotionTimelineEntryTests(SimpleTestCase):
    """What the serializer puts on the entry is what the card can render."""

    def _entry(self, agent_config: dict, params: dict | None = None) -> dict:
        from unittest.mock import MagicMock

        from console.agent_chat.timeline import _serialize_step_entry

        step = MagicMock()
        step.id = "step-1"
        step.description = "emotion update"
        step.created_at = None
        tool_call = MagicMock()
        tool_call.tool_name = "sqlite_batch"
        tool_call.tool_params = params if params is not None else EMOTION_SQL
        tool_call.result = {"status": "ok"}
        tool_call.status = "complete"
        tool_call.display_metadata = {"agent_config": agent_config}
        env = MagicMock()
        env.step = step
        env.tool_call = tool_call
        env.cursor.encode.return_value = "1:step:step-1"
        return _serialize_step_entry(env, {})

    def test_emotion_reaches_the_entry(self):
        entry = self._entry({"emotion": "\U0001F60A", "emotion_timeout_seconds": 3600})

        self.assertEqual(entry["emotion"], "\U0001F60A")
        self.assertEqual(entry["emotionTimeoutSeconds"], 3600)

    def test_clearing_an_emotion_is_representable(self):
        entry = self._entry({"emotion": None})

        self.assertIn("emotion", entry)
        self.assertIsNone(entry["emotion"])

    def test_an_unrelated_config_write_carries_no_emotion_key(self):
        """Otherwise every charter edit would look like a mood change to the card."""
        entry = self._entry({"charter": "just the charter"})

        self.assertNotIn("emotion", entry)

    def test_stale_metadata_on_a_charter_write_is_not_a_mood(self):
        """Steps persisted during the #462 regression window already carry `emotion: None`.

        Their display metadata cannot be trusted, but their SQL can: if the statement never
        assigned an emotion column, the entry must not claim a mood settled (bug #462, the
        "Jordan let their mood settle" cards on charter updates).
        """
        entry = self._entry(
            {"charter": "c", "emotion": None},
            params={"sql": "UPDATE __agent_config SET charter = 'new charter' WHERE id = 1;"},
        )

        self.assertNotIn("emotion", entry)
        self.assertNotIn("emotionTimeoutSeconds", entry)

    def test_stale_metadata_on_a_timer_write_is_not_a_mood(self):
        entry = self._entry(
            {"charter": "c", "emotion": None},
            params={"sql": "INSERT INTO __agent_schedules (name, cron) VALUES ('recheck', '0 9 * * *');"},
        )

        self.assertNotIn("emotion", entry)

    def test_a_real_mood_clear_still_reads_as_cleared(self):
        """The guard must not swallow deliberate clears, which share the `None` value."""
        entry = self._entry(
            {"emotion": None},
            params={"sql": "UPDATE __agent_config SET emotion = NULL WHERE id = 1;"},
        )

        self.assertIn("emotion", entry)
        self.assertIsNone(entry["emotion"])
