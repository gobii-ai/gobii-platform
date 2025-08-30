from datetime import timedelta
from django.test import TestCase, override_settings, tag
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.conf import settings

from api.agent.core.step_compaction import ensure_steps_compacted
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentStepSnapshot,
    PersistentAgentToolCall,
    PersistentAgentCronTrigger,
    PersistentAgentSystemStep,
)

User = get_user_model()


@override_settings(PA_RAW_STEP_LIMIT=5)
@tag("batch_step_compaction")
class StepCompactionTests(TestCase):
    """Unit-tests for on-demand step history compaction."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="tester@example.com",
            email="tester@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Persistent-1",
            charter="do things",
            browser_use_agent=self.browser_agent,
            created_at=timezone.now(),
        )

    def _make_tool_call_step(self, ts, tool_name="test_tool", result_text="success"):
        """Create a tool call step with the given timestamp."""
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description=f"Called {tool_name}",
            created_at=ts,
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name=tool_name,
            tool_params={"param1": "value1"},
            result=result_text,
        )
        return step

    def _make_cron_trigger_step(self, ts, cron_expr="* * * * *"):
        """Create a cron trigger step with the given timestamp."""
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Cron triggered",
            created_at=ts,
        )
        PersistentAgentCronTrigger.objects.create(
            step=step,
            cron_expression=cron_expr,
        )
        return step

    def _make_system_step(self, ts, code="TEST", notes="test system step"):
        """Create a system step with the given timestamp."""
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="System step",
            created_at=ts,
        )
        PersistentAgentSystemStep.objects.create(
            step=step,
            code=code,
            notes=notes,
        )
        return step

    def _make_generic_step(self, ts, description="generic step"):
        """Create a generic step (no satellite record) with the given timestamp."""
        return PersistentAgentStep.objects.create(
            agent=self.agent,
            description=description,
            created_at=ts,
        )

    @tag("batch_step_compaction")
    def test_compaction_triggered_when_over_limit(self):
        """When raw steps > limit, a new snapshot is created."""
        # NB: RAW_STEP_LIMIT is evaluated at import time; instead we read from settings
        from api.agent.core.step_compaction import MAX_TOOL_RESULT_CHARS

        # Sanity-check: no snapshots at start
        self.assertEqual(PersistentAgentStepSnapshot.objects.count(), 0)

        # Create one more step than the limit, mixing different step types
        num_steps = settings.PA_RAW_STEP_LIMIT + 1
        for i in range(num_steps):
            ts = self.agent.created_at + timedelta(seconds=i + 1)
            if i % 4 == 0:
                self._make_tool_call_step(ts, f"tool_{i}")
            elif i % 4 == 1:
                self._make_cron_trigger_step(ts)
            elif i % 4 == 2:
                self._make_system_step(ts, f"CODE_{i}")
            else:
                self._make_generic_step(ts, f"generic step {i}")

        # Run compaction
        ensure_steps_compacted(agent=self.agent)

        # A snapshot should have been created
        self.assertEqual(PersistentAgentStepSnapshot.objects.count(), 1)
        snapshot = PersistentAgentStepSnapshot.objects.first()

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.agent, self.agent)
        self.assertIsNone(snapshot.previous_snapshot)
        self.assertIn(f"--- Recent Steps ({num_steps}) ---", snapshot.summary)

        # Check snapshot_until is correct (created_at of the last step)
        last_step = PersistentAgentStep.objects.order_by("created_at").last()
        self.assertEqual(snapshot.snapshot_until, last_step.created_at)

    @tag("batch_step_compaction")
    def test_no_compaction_when_at_or_below_limit(self):
        """No snapshot should be created when raw steps <= limit."""
        # NB: RAW_STEP_LIMIT is evaluated at import time; instead we read from settings
        from api.agent.core.step_compaction import MAX_TOOL_RESULT_CHARS

        # Create exactly the limit number of steps
        for i in range(settings.PA_RAW_STEP_LIMIT):
            ts = self.agent.created_at + timedelta(seconds=i + 1)
            self._make_tool_call_step(ts, f"tool_{i}")

        # Run compaction
        ensure_steps_compacted(agent=self.agent)

        # Still no snapshots expected
        self.assertEqual(PersistentAgentStepSnapshot.objects.count(), 0)

    @tag("batch_step_compaction")
    def test_incremental_compaction_with_existing_snapshot(self):
        """A second compaction should create a new snapshot linked to the previous one."""
        # NB: RAW_STEP_LIMIT is evaluated at import time; instead we read from settings
        from api.agent.core.step_compaction import MAX_TOOL_RESULT_CHARS

        # ------------------- First batch ------------------- #
        first_batch = settings.PA_RAW_STEP_LIMIT + 1
        for i in range(first_batch):
            ts = self.agent.created_at + timedelta(seconds=i + 1)
            self._make_tool_call_step(ts, f"batch1_tool_{i}")

        ensure_steps_compacted(agent=self.agent)
        self.assertEqual(PersistentAgentStepSnapshot.objects.count(), 1)
        first_snapshot = PersistentAgentStepSnapshot.objects.first()
        self.assertIsNotNone(first_snapshot)

        # ------------------ Second batch ------------------ #
        second_batch = settings.PA_RAW_STEP_LIMIT + 2  # different size to distinguish
        start_sec = first_batch + 1
        for i in range(second_batch):
            ts = self.agent.created_at + timedelta(seconds=start_sec + i)
            self._make_cron_trigger_step(ts, f"0 {i} * * *")

        ensure_steps_compacted(agent=self.agent)

        # We should now have exactly two snapshots.
        self.assertEqual(PersistentAgentStepSnapshot.objects.count(), 2)
        latest_snapshot = PersistentAgentStepSnapshot.objects.order_by("-snapshot_until").first()
        self.assertIsNotNone(latest_snapshot)
        self.assertEqual(latest_snapshot.previous_snapshot, first_snapshot)

        # Summary should include both the previous snapshot's content and the new content.
        self.assertIn(first_snapshot.summary, latest_snapshot.summary)
        self.assertIn(f"--- Recent Steps ({second_batch}) ---", latest_snapshot.summary)

        # snapshot_until should correspond to the last step we've created
        last_step = PersistentAgentStep.objects.order_by("created_at").last()
        self.assertEqual(latest_snapshot.snapshot_until, last_step.created_at)

    def test_mixed_step_types_in_summary(self):
        """Test that different step types are correctly formatted in the summary."""
        # NB: RAW_STEP_LIMIT is evaluated at import time; instead we read from settings
        from api.agent.core.step_compaction import MAX_TOOL_RESULT_CHARS

        # Create one more than limit with specific step types
        num_steps = settings.PA_RAW_STEP_LIMIT + 1
        base_time = self.agent.created_at

        # Create one of each step type
        self._make_tool_call_step(
            base_time + timedelta(seconds=1), 
            "read_file", 
            "File contents: Hello World\nThis is a test file"
        )
        self._make_cron_trigger_step(
            base_time + timedelta(seconds=2), 
            "0 */6 * * *"
        )
        self._make_system_step(
            base_time + timedelta(seconds=3), 
            "PROCESS_EVENTS", 
            "Processing event queue"
        )
        self._make_generic_step(
            base_time + timedelta(seconds=4), 
            "Some generic operation"
        )

        # Fill the rest with tool calls to exceed limit
        for i in range(4, num_steps):
            self._make_tool_call_step(
                base_time + timedelta(seconds=i + 1), 
                f"tool_{i}"
            )

        ensure_steps_compacted(agent=self.agent)

        snapshot = PersistentAgentStepSnapshot.objects.first()
        self.assertIsNotNone(snapshot)

        # Verify different step types appear in summary with correct emojis
        self.assertIn("🔧 read_file", snapshot.summary)  # tool call
        self.assertIn("⏰ Cron: 0 */6 * * *", snapshot.summary)  # cron trigger
        self.assertIn("⚙️  System[PROCESS_EVENTS]", snapshot.summary)  # system step
        self.assertIn("📝 Some generic operation", snapshot.summary)  # generic step

    def test_large_tool_result_truncation(self):
        """Test that large tool results are properly truncated."""
        # NB: RAW_STEP_LIMIT is evaluated at import time; instead we read from settings
        from api.agent.core.step_compaction import MAX_TOOL_RESULT_CHARS

        # Create a large result that exceeds MAX_TOOL_RESULT_CHARS
        large_result = "x" * (MAX_TOOL_RESULT_CHARS + 1000)
        
        # Create enough steps to trigger compaction
        for i in range(settings.PA_RAW_STEP_LIMIT + 1):
            ts = self.agent.created_at + timedelta(seconds=i + 1)
            if i == 0:
                # First step has the large result
                self._make_tool_call_step(ts, "large_tool", large_result)
            else:
                self._make_tool_call_step(ts, f"tool_{i}")

        ensure_steps_compacted(agent=self.agent)

        snapshot = PersistentAgentStepSnapshot.objects.first()
        self.assertIsNotNone(snapshot)
        
        # The summary should contain truncated marker
        self.assertIn("… (truncated) …", snapshot.summary)

    def test_custom_summarise_function(self):
        """Test that a custom summarise function is used when provided."""
        # NB: RAW_STEP_LIMIT is evaluated at import time; instead we read from settings
        from api.agent.core.step_compaction import MAX_TOOL_RESULT_CHARS

        def custom_summarise(previous, steps):
            return f"CUSTOM: {len(steps)} steps processed"

        # Create enough steps to trigger compaction
        for i in range(settings.PA_RAW_STEP_LIMIT + 1):
            ts = self.agent.created_at + timedelta(seconds=i + 1)
            self._make_tool_call_step(ts, f"tool_{i}")

        ensure_steps_compacted(agent=self.agent, summarise_fn=custom_summarise)

        snapshot = PersistentAgentStepSnapshot.objects.first()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.summary, f"CUSTOM: {settings.PA_RAW_STEP_LIMIT + 1} steps processed")

    def test_race_condition_detection(self):
        """Test that race conditions are properly detected and handled."""
        # NB: RAW_STEP_LIMIT is evaluated at import time; instead we read from settings
        from api.agent.core.step_compaction import MAX_TOOL_RESULT_CHARS

        # Create enough steps to trigger compaction
        for i in range(settings.PA_RAW_STEP_LIMIT + 1):
            ts = self.agent.created_at + timedelta(seconds=i + 1)
            self._make_tool_call_step(ts, f"tool_{i}")

        # Get the timestamp of the last step
        last_step = PersistentAgentStep.objects.order_by("created_at").last()

        # Manually create a snapshot that would indicate another process beat us
        PersistentAgentStepSnapshot.objects.create(
            agent=self.agent,
            previous_snapshot=None,
            snapshot_until=last_step.created_at,
            summary="Manual snapshot",
        )

        # Running compaction should detect the race and not create another snapshot
        ensure_steps_compacted(agent=self.agent)

        # Should still only have the one snapshot we created manually
        self.assertEqual(PersistentAgentStepSnapshot.objects.count(), 1)
        snapshot = PersistentAgentStepSnapshot.objects.first()
        self.assertEqual(snapshot.summary, "Manual snapshot") 
