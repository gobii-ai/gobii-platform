from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentStep
from console.timeline import fetch_timeline_window


@tag("batch_timeline_window")
class TimelineWindowScalabilityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="timeline_test@example.com",
            email="timeline_test@example.com",
            password="pass1234",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Timeline Test Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Timeline Test Agent",
            charter="Ensure timeline window tests can run",
            browser_use_agent=self.browser_agent,
        )
        self.base_time = timezone.now()

    def _make_step(self, minutes_offset: int, description: str) -> PersistentAgentStep:
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description=description,
        )
        target_time = self.base_time + timedelta(minutes=minutes_offset)
        PersistentAgentStep.objects.filter(pk=step.pk).update(created_at=target_time)
        step.refresh_from_db()
        return step

    def test_older_window_sets_has_more_newer_flag(self):
        # Create three chronological steps so the window has older history available.
        self._make_step(-5, "oldest step")
        middle_step = self._make_step(-3, "middle step")
        newest_step = self._make_step(-1, "newest step")

        initial_window = fetch_timeline_window(self.agent, limit=2)

        self.assertEqual(initial_window.events[-1].payload, newest_step)
        self.assertEqual(initial_window.events[0].payload, middle_step)

        older_window = fetch_timeline_window(
            self.agent,
            limit=2,
            direction="older",
            cursor=initial_window.window_oldest_cursor,
        )

        self.assertTrue(
            older_window.has_more_newer,
            "Loading older history should advertise the presence of newer items for forward navigation.",
        )
        self.assertIsNotNone(older_window.window_newest_cursor)

    def test_older_window_without_results_keeps_has_more_newer_false(self):
        # Only two steps; asking for additional older history should return empty results.
        self._make_step(-2, "earliest step")
        latest_step = self._make_step(-1, "latest step")

        initial_window = fetch_timeline_window(self.agent, limit=5)

        older_window = fetch_timeline_window(
            self.agent,
            limit=5,
            direction="older",
            cursor=initial_window.window_oldest_cursor,
        )

        self.assertFalse(
            older_window.has_more_newer,
            "When no additional older events exist, the forward navigation flag should remain false.",
        )
        self.assertEqual(
            older_window.events,
            [],
            "No additional events should be returned when history is exhausted.",
        )
        self.assertEqual(initial_window.events[-1].payload, latest_step)

    def test_newer_window_empty_preserves_has_more_older(self):
        # Create timeline and fetch older slice so cursor points to middle.
        steps = [
            self._make_step(-5, "oldest step"),
            self._make_step(-4, "step -4"),
            self._make_step(-3, "step -3"),
            self._make_step(-2, "step -2"),
            self._make_step(-1, "newest step"),
            self._make_step(0, "latest step"),
        ]

        initial_window = fetch_timeline_window(self.agent, limit=3)
        self.assertTrue(initial_window.has_more_older)

        # Ask for newer events beyond the newest cursor (there aren't any yet).
        newer_window = fetch_timeline_window(
            self.agent,
            limit=3,
            direction="newer",
            cursor=initial_window.window_newest_cursor,
        )

        self.assertEqual(newer_window.events, [])
        self.assertTrue(
            newer_window.has_more_older,
            "Even when no newer events exist, older history should remain accessible.",
        )

    def test_multiple_older_windows_exhaust_history(self):
        # Build a short timeline of five steps spaced a minute apart.
        steps = [
            self._make_step(-5, "oldest step"),
            self._make_step(-4, "step -4"),
            self._make_step(-3, "step -3"),
            self._make_step(-2, "step -2"),
            self._make_step(-1, "newest step"),
        ]

        initial_window = fetch_timeline_window(self.agent, limit=2)

        self.assertEqual(len(initial_window.events), 2)
        self.assertTrue(initial_window.has_more_older)
        self.assertEqual(
            [event.payload for event in initial_window.events],
            [steps[3], steps[4]],
        )

        first_cursor = initial_window.window_oldest_cursor
        self.assertIsNotNone(first_cursor)

        first_older = fetch_timeline_window(
            self.agent,
            limit=2,
            direction="older",
            cursor=first_cursor,
        )

        self.assertEqual(len(first_older.events), 2)
        self.assertTrue(first_older.has_more_older)
        self.assertTrue(first_older.has_more_newer)
        self.assertEqual(
            [event.payload for event in first_older.events],
            [steps[1], steps[2]],
        )

        second_cursor = first_older.window_oldest_cursor
        self.assertIsNotNone(second_cursor)

        second_older = fetch_timeline_window(
            self.agent,
            limit=2,
            direction="older",
            cursor=second_cursor,
        )

        self.assertEqual(len(second_older.events), 1)
        self.assertFalse(second_older.has_more_older)
        self.assertTrue(second_older.has_more_newer)
        self.assertEqual(second_older.events[0].payload, steps[0])

        final_cursor = second_older.window_oldest_cursor
        self.assertIsNotNone(final_cursor)

        exhausted = fetch_timeline_window(
            self.agent,
            limit=2,
            direction="older",
            cursor=final_cursor,
        )

        self.assertEqual(exhausted.events, [])
        self.assertFalse(exhausted.has_more_older)
        self.assertFalse(exhausted.has_more_newer)
