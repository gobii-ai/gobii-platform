from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from django.test import TestCase, tag

from console.templatetags.agent_extras import group_timeline_events


def _make_tool_event(base_time, cursor_suffix, *, tool_name="sqlite_batch"):
    timestamp = base_time + timedelta(minutes=cursor_suffix)
    tool_call = SimpleNamespace(tool_name=tool_name, tool_params={}, result="")
    step = SimpleNamespace(tool_call=tool_call)
    return SimpleNamespace(
        kind="step",
        timestamp=timestamp,
        cursor=f"step-{cursor_suffix}",
        message=None,
        step=step,
    )


def _make_message_event(base_time, cursor_suffix):
    timestamp = base_time + timedelta(minutes=cursor_suffix)
    return SimpleNamespace(
        kind="message",
        timestamp=timestamp,
        cursor=f"msg-{cursor_suffix}",
        message=SimpleNamespace(body="hello"),
        step=None,
    )


@tag("batch_group_timeline_events")
class GroupTimelineEventsBatchingTests(TestCase):
    def setUp(self):
        self.base_time = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)

    def test_clusters_under_threshold_remain_inline(self):
        events = [_make_tool_event(self.base_time, idx) for idx in range(4)]

        grouped = group_timeline_events(events)

        self.assertEqual(len(grouped), 1)
        cluster = grouped[0]
        self.assertEqual(cluster["type"], "steps")
        self.assertEqual(cluster["entry_count"], 4)
        self.assertFalse(cluster["collapsible"])

    def test_clusters_at_threshold_are_collapsible(self):
        events = [_make_tool_event(self.base_time, idx) for idx in range(5)]

        grouped = group_timeline_events(events)

        self.assertEqual(len(grouped), 1)
        cluster = grouped[0]
        self.assertTrue(cluster["collapsible"])
        self.assertEqual(cluster["entry_count"], 5)
        self.assertIsNotNone(cluster["earliest_timestamp"])
        self.assertEqual(cluster["earliest_timestamp"], events[0].timestamp)
        self.assertEqual(cluster["collapse_threshold"], 5)

    def test_message_breaks_cluster(self):
        events = [_make_tool_event(self.base_time, idx) for idx in range(3)]
        events.append(_make_message_event(self.base_time, 10))
        events.extend(_make_tool_event(self.base_time, 20 + idx) for idx in range(5))

        grouped = group_timeline_events(events)

        self.assertEqual(len(grouped), 3)
        first_cluster = grouped[0]
        message_item = grouped[1]
        second_cluster = grouped[2]

        self.assertEqual(first_cluster["entry_count"], 3)
        self.assertFalse(first_cluster["collapsible"])
        self.assertEqual(message_item["type"], "message")
        self.assertTrue(second_cluster["collapsible"])
        self.assertEqual(second_cluster["entry_count"], 5)
