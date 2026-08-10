from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings, tag
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.conf import settings
from openai import OpenAIError

from api.agent.core.compaction_exceptions import CompactionSummaryError
from api.agent.core.internal_reasoning import build_internal_reasoning_description
from api.agent.core.step_compaction import ensure_steps_compacted, llm_summarise_steps
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


@override_settings(PA_RAW_STEP_LIMIT=5, PA_STEP_COMPACTION_TAIL=2)
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

    @staticmethod
    def _set_step_time(step, ts):
        PersistentAgentStep.objects.filter(id=step.id).update(created_at=ts)
        step.created_at = ts
        return step

    def _compact(self):
        def summarize(previous, steps, safety_identifier):
            lines = [f"--- Recent Steps ({len(steps)}) ---"]
            lines.extend(f"• {step.to_summary_str()}" for step in steps)
            if safety_identifier:
                lines.append(f"Safety ID: {safety_identifier}")
            return previous + ("\n" if previous else "") + "\n".join(lines)

        ensure_steps_compacted(agent=self.agent, summarise_fn=summarize)

    @tag("batch_step_compaction")
    def test_compaction_triggered_when_over_limit(self):
        """When raw steps > limit, a new snapshot is created."""
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
        self._compact()

        # A snapshot should have been created
        self.assertEqual(PersistentAgentStepSnapshot.objects.count(), 1)
        snapshot = PersistentAgentStepSnapshot.objects.first()

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.agent, self.agent)
        self.assertIsNone(snapshot.previous_snapshot)
        compacted_count = num_steps - settings.PA_STEP_COMPACTION_TAIL
        self.assertIn(f"--- Recent Steps ({compacted_count}) ---", snapshot.summary)

        # Check snapshot_until is correct (timestamp of the last compacted step)
        ordered = list(PersistentAgentStep.objects.order_by("created_at"))
        expected_until = ordered[-(settings.PA_STEP_COMPACTION_TAIL + 1)].created_at
        self.assertEqual(snapshot.snapshot_until, expected_until)

        remaining = PersistentAgentStep.objects.filter(
            created_at__gt=snapshot.snapshot_until
        ).count()
        self.assertEqual(remaining, settings.PA_STEP_COMPACTION_TAIL)

    @tag("batch_step_compaction")
    def test_no_compaction_when_at_or_below_limit(self):
        """No snapshot should be created when raw steps <= limit."""
        # Create exactly the limit number of steps
        for i in range(settings.PA_RAW_STEP_LIMIT):
            ts = self.agent.created_at + timedelta(seconds=i + 1)
            self._make_tool_call_step(ts, f"tool_{i}")

        # Run compaction
        self._compact()

        # Still no snapshots expected
        self.assertEqual(PersistentAgentStepSnapshot.objects.count(), 0)

    @tag("batch_step_compaction")
    def test_incremental_compaction_with_existing_snapshot(self):
        """A second compaction should create a new snapshot linked to the previous one."""
        # ------------------- First batch ------------------- #
        first_batch = settings.PA_RAW_STEP_LIMIT + 1
        for i in range(first_batch):
            ts = self.agent.created_at + timedelta(seconds=i + 1)
            self._make_tool_call_step(ts, f"batch1_tool_{i}")

        self._compact()
        self.assertEqual(PersistentAgentStepSnapshot.objects.count(), 1)
        first_snapshot = PersistentAgentStepSnapshot.objects.first()
        self.assertIsNotNone(first_snapshot)

        # ------------------ Second batch ------------------ #
        second_batch = settings.PA_RAW_STEP_LIMIT + 2  # different size to distinguish
        start_sec = first_batch + 1
        for i in range(second_batch):
            ts = self.agent.created_at + timedelta(seconds=start_sec + i)
            self._make_cron_trigger_step(ts, f"0 {i} * * *")

        self._compact()

        # We should now have exactly two snapshots.
        self.assertEqual(PersistentAgentStepSnapshot.objects.count(), 2)
        latest_snapshot = PersistentAgentStepSnapshot.objects.order_by("-snapshot_until").first()
        self.assertIsNotNone(latest_snapshot)
        self.assertEqual(latest_snapshot.previous_snapshot, first_snapshot)

        # Summary should include both the previous snapshot's content and the new content.
        self.assertIn(first_snapshot.summary, latest_snapshot.summary)
        expected_compacted = (
            PersistentAgentStep.objects.filter(
                created_at__gt=first_snapshot.snapshot_until
            ).count()
            - settings.PA_STEP_COMPACTION_TAIL
        )
        self.assertIn(f"--- Recent Steps ({expected_compacted}) ---", latest_snapshot.summary)

        # snapshot_until should correspond to the last compacted step
        ordered = list(PersistentAgentStep.objects.order_by("created_at"))
        expected_until = ordered[-(settings.PA_STEP_COMPACTION_TAIL + 1)].created_at
        self.assertEqual(latest_snapshot.snapshot_until, expected_until)

    def test_mixed_step_types_in_summary(self):
        """Test that different step types are correctly formatted in the summary."""
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

        self._compact()

        snapshot = PersistentAgentStepSnapshot.objects.first()
        self.assertIsNotNone(snapshot)

        # Verify different step types appear in summary with correct emojis
        self.assertIn("🔧 read_file", snapshot.summary)  # tool call
        self.assertIn("⏰ Cron: 0 */6 * * *", snapshot.summary)  # cron trigger
        self.assertIn("⚙️  System[PROCESS_EVENTS]", snapshot.summary)  # system step
        self.assertIn("📝 Some generic operation", snapshot.summary)  # generic step

    @tag("batch_step_compaction")
    def test_summary_omits_internal_reasoning_steps(self):
        """Internal reasoning should not be copied into compacted step summaries."""
        base_time = self.agent.created_at

        self._make_generic_step(
            base_time + timedelta(seconds=1),
            build_internal_reasoning_description("draft reply"),
        )
        self._make_tool_call_step(
            base_time + timedelta(seconds=2),
            "search_tools",
            "found matches",
        )
        self._make_generic_step(
            base_time + timedelta(seconds=3),
            "Reviewed permit requirements",
        )
        self._make_cron_trigger_step(
            base_time + timedelta(seconds=4),
            "0 */6 * * *",
        )
        self._make_generic_step(
            base_time + timedelta(seconds=5),
            build_internal_reasoning_description("ask for clarification"),
        )
        self._make_system_step(
            base_time + timedelta(seconds=6),
            "PROCESS_EVENTS",
            "processed queue",
        )

        self._compact()

        snapshot = PersistentAgentStepSnapshot.objects.first()
        self.assertIsNotNone(snapshot)
        self.assertNotIn("draft reply", snapshot.summary)
        self.assertNotIn("ask for clarification", snapshot.summary)
        self.assertIn("🔧 search_tools", snapshot.summary)
        self.assertIn("📝 Reviewed permit requirements", snapshot.summary)

    @override_settings(PA_RAW_STEP_LIMIT=3, PA_STEP_COMPACTION_TAIL=0)
    @tag("batch_step_compaction")
    def test_reasoning_only_compaction_preserves_existing_summary(self):
        """A reasoning-only batch should advance the snapshot without changing its summary."""
        base_time = self.agent.created_at
        for idx in range(4):
            self._make_generic_step(
                base_time + timedelta(seconds=idx + 1),
                f"Visible step {idx}",
            )

        self._compact()

        first_snapshot = PersistentAgentStepSnapshot.objects.order_by("-snapshot_until").first()
        self.assertIsNotNone(first_snapshot)

        reasoning_start = base_time + timedelta(seconds=10)
        for idx in range(4):
            self._make_generic_step(
                reasoning_start + timedelta(seconds=idx),
                build_internal_reasoning_description(f"reasoning-only {idx}"),
            )

        self._compact()

        latest_snapshot = PersistentAgentStepSnapshot.objects.order_by("-snapshot_until").first()
        self.assertIsNotNone(latest_snapshot)
        self.assertNotEqual(latest_snapshot.id, first_snapshot.id)
        self.assertEqual(latest_snapshot.summary, first_snapshot.summary)
        self.assertGreater(latest_snapshot.snapshot_until, first_snapshot.snapshot_until)

    @override_settings(PA_RAW_STEP_LIMIT=3, PA_STEP_COMPACTION_TAIL=0)
    def test_initial_reasoning_only_history_does_not_create_empty_snapshot(self):
        base_time = self.agent.created_at
        for index in range(4):
            self._make_generic_step(
                base_time + timedelta(seconds=index + 1),
                build_internal_reasoning_description(f"reasoning-only {index}"),
            )

        self._compact()

        self.assertFalse(PersistentAgentStepSnapshot.objects.exists())

    def test_large_tool_result_truncation(self):
        """Test that large tool results are properly truncated."""
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

        self._compact()

        snapshot = PersistentAgentStepSnapshot.objects.first()
        self.assertIsNotNone(snapshot)
        
        # The summary should contain truncated marker
        self.assertIn("… (truncated) …", snapshot.summary)

    def test_custom_summarise_function(self):
        """Test that a custom summarise function is used when provided."""
        def custom_summarise(previous, steps, safety_identifier):
            return f"CUSTOM: {len(steps)} steps processed"

        # Create enough steps to trigger compaction
        for i in range(settings.PA_RAW_STEP_LIMIT + 1):
            ts = self.agent.created_at + timedelta(seconds=i + 1)
            self._make_tool_call_step(ts, f"tool_{i}")

        ensure_steps_compacted(agent=self.agent, summarise_fn=custom_summarise, safety_identifier="123")

        snapshot = PersistentAgentStepSnapshot.objects.first()
        self.assertIsNotNone(snapshot)
        expected_compacted = settings.PA_RAW_STEP_LIMIT + 1 - settings.PA_STEP_COMPACTION_TAIL
        self.assertEqual(snapshot.summary, f"CUSTOM: {expected_compacted} steps processed")

    def test_race_condition_detection(self):
        """Test that race conditions are properly detected and handled."""
        # Create enough steps to trigger compaction
        for i in range(settings.PA_RAW_STEP_LIMIT + 1):
            ts = self.agent.created_at + timedelta(seconds=i + 1)
            self._make_tool_call_step(ts, f"tool_{i}")

        # Get the timestamp of the last compacted step
        ordered = list(PersistentAgentStep.objects.order_by("created_at"))
        expected_until = ordered[-(settings.PA_STEP_COMPACTION_TAIL + 1)].created_at

        # Manually create a snapshot that would indicate another process beat us
        PersistentAgentStepSnapshot.objects.create(
            agent=self.agent,
            previous_snapshot=None,
            snapshot_until=expected_until,
            summary="Manual snapshot",
        )

        # Running compaction should detect the race and not create another snapshot
        self._compact()

        # Should still only have the one snapshot we created manually
        self.assertEqual(PersistentAgentStepSnapshot.objects.count(), 1)
        snapshot = PersistentAgentStepSnapshot.objects.first()
        self.assertEqual(snapshot.summary, "Manual snapshot") 

    @override_settings(PA_RAW_STEP_LIMIT=3, PA_STEP_COMPACTION_TAIL=0)
    def test_pending_tool_call_stays_on_raw_side_of_snapshot(self):
        base_time = self.agent.created_at
        first = self._set_step_time(
            self._make_generic_step(base_time, "stable one"),
            base_time + timedelta(seconds=1),
        )
        second = self._set_step_time(
            self._make_generic_step(base_time, "stable two"),
            base_time + timedelta(seconds=2),
        )
        pending = self._set_step_time(
            self._make_tool_call_step(base_time, "pending_tool"),
            base_time + timedelta(seconds=3),
        )
        pending.tool_call.status = PersistentAgentToolCall.Status.PENDING
        pending.tool_call.save(update_fields=["status"])
        latest = self._set_step_time(
            self._make_generic_step(base_time, "stable after pending"),
            base_time + timedelta(seconds=4),
        )

        self._compact()

        snapshot = PersistentAgentStepSnapshot.objects.get()
        self.assertEqual(snapshot.snapshot_until, second.created_at)
        self.assertEqual(
            set(
                PersistentAgentStep.objects.filter(
                    created_at__gt=snapshot.snapshot_until
                ).values_list("id", flat=True)
            ),
            {pending.id, latest.id},
        )
        self.assertIn("stable one", snapshot.summary)
        self.assertIn("stable two", snapshot.summary)
        self.assertNotIn("pending_tool", snapshot.summary)
        self.assertEqual(first.agent_id, self.agent.id)

        pending.tool_call.status = PersistentAgentToolCall.Status.COMPLETE
        pending.tool_call.save(update_fields=["status"])
        for second_offset in (5, 6):
            added = self._make_generic_step(base_time, f"stable {second_offset}")
            self._set_step_time(added, base_time + timedelta(seconds=second_offset))

        self._compact()

        finalized_snapshot = PersistentAgentStepSnapshot.objects.latest("snapshot_until")
        self.assertGreater(finalized_snapshot.snapshot_until, pending.created_at)
        self.assertIn("pending_tool", finalized_snapshot.summary)

    @override_settings(PA_RAW_STEP_LIMIT=4, PA_STEP_COMPACTION_TAIL=2)
    def test_cutoff_includes_every_step_with_the_same_timestamp(self):
        base_time = self.agent.created_at
        shared_time = base_time + timedelta(seconds=3)
        timestamps = (
            base_time + timedelta(seconds=1),
            base_time + timedelta(seconds=2),
            shared_time,
            shared_time,
            base_time + timedelta(seconds=4),
        )
        steps = []
        for index, timestamp in enumerate(timestamps):
            step = self._make_generic_step(base_time, f"step {index}")
            steps.append(self._set_step_time(step, timestamp))
        summarized_ids = []

        def summarize(_previous, selected, _safety_identifier):
            summarized_ids.extend(step.step_id for step in selected)
            return "summary"

        ensure_steps_compacted(agent=self.agent, summarise_fn=summarize)

        snapshot = PersistentAgentStepSnapshot.objects.get()
        self.assertEqual(snapshot.snapshot_until, shared_time)
        self.assertCountEqual(
            summarized_ids,
            [str(step.id) for step in steps[:4]],
        )
        self.assertEqual(
            list(
                PersistentAgentStep.objects.filter(
                    created_at__gt=snapshot.snapshot_until
                ).values_list("description", flat=True)
            ),
            ["step 4"],
        )

    @override_settings(PA_RAW_STEP_LIMIT=3, PA_STEP_COMPACTION_TAIL=1)
    def test_backdated_step_during_summary_aborts_snapshot(self):
        base_time = self.agent.created_at
        for index in range(4):
            step = self._make_generic_step(base_time, f"original {index}")
            self._set_step_time(step, base_time + timedelta(seconds=index + 1))

        def summarize(_previous, selected, _safety_identifier):
            inserted = self._make_generic_step(base_time, "arrived during summary")
            self._set_step_time(inserted, selected[-1].created_at)
            return "summary"

        ensure_steps_compacted(agent=self.agent, summarise_fn=summarize)

        self.assertFalse(PersistentAgentStepSnapshot.objects.exists())
        self.assertEqual(PersistentAgentStep.objects.count(), 5)

    @override_settings(PA_RAW_STEP_LIMIT=3, PA_STEP_COMPACTION_TAIL=1)
    def test_step_arriving_after_cutoff_remains_raw(self):
        base_time = self.agent.created_at
        for index in range(4):
            step = self._make_generic_step(base_time, f"original {index}")
            self._set_step_time(step, base_time + timedelta(seconds=index + 1))

        def summarize(_previous, selected, _safety_identifier):
            inserted = self._make_generic_step(base_time, "arrived after cutoff")
            self._set_step_time(
                inserted,
                selected[-1].created_at + timedelta(seconds=10),
            )
            return "summary"

        ensure_steps_compacted(agent=self.agent, summarise_fn=summarize)

        snapshot = PersistentAgentStepSnapshot.objects.get()
        self.assertCountEqual(
            list(
                PersistentAgentStep.objects.filter(
                    created_at__gt=snapshot.snapshot_until,
                ).values_list("description", flat=True)
            ),
            ["original 3", "arrived after cutoff"],
        )

    @override_settings(PA_RAW_STEP_LIMIT=3, PA_STEP_COMPACTION_TAIL=1)
    def test_empty_summary_does_not_advance_snapshot(self):
        base_time = self.agent.created_at
        for index in range(4):
            step = self._make_generic_step(base_time, f"step {index}")
            self._set_step_time(step, base_time + timedelta(seconds=index + 1))

        with self.assertRaises(CompactionSummaryError):
            ensure_steps_compacted(
                agent=self.agent,
                summarise_fn=lambda _previous, _steps, _safety_identifier: "",
            )

        self.assertFalse(PersistentAgentStepSnapshot.objects.exists())

    def test_llm_compaction_preserves_active_scoped_directives(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))]
        )

        with patch(
            "api.agent.core.step_compaction.get_summarization_llm_config",
            return_value=("openai", "openai/test", {}),
        ), patch(
            "api.agent.core.step_compaction.run_completion",
            return_value=response,
        ) as run_completion_mock, patch(
            "api.agent.core.step_compaction.log_agent_completion",
            return_value=(None, {}),
        ), patch("api.agent.core.step_compaction.set_usage_span_attributes"):
            summary = llm_summarise_steps("", [], agent=self.agent)

        self.assertEqual(summary, "summary")
        system_prompt = run_completion_mock.call_args.kwargs["messages"][0]["content"]
        self.assertIn("still-operative scoped directives", system_prompt)
        self.assertIn("stop/do-not-act instructions", system_prompt)
        self.assertIn("actor or source, scope identifier, and effective constraint", system_prompt)
        self.assertIn("until explicitly superseded, expired, or reassigned", system_prompt)
        self.assertIn("no continuing consequence", system_prompt)
        self.assertIn("Copy opaque values", system_prompt)
        self.assertIn("never alter their case, punctuation, spacing, or characters", system_prompt)
        self.assertIn("never mention replaced values", system_prompt)
        self.assertIn("Aggregate repeated no-op polling or cron cycles", system_prompt)
        self.assertIn("never weaken forbidden work", system_prompt)
        self.assertIn("must be at most 2,000 characters", system_prompt)
        self.assertIn("silently audit the draft", system_prompt)
        self.assertIn("delete every superseded value", system_prompt)
        self.assertIn("compare every opaque value character-for-character", system_prompt)
        self.assertIn("preserve distinctions between lifecycle states", system_prompt)
        self.assertIn("without inferring outcomes", system_prompt)

    def test_llm_failure_raises_retryable_compaction_error(self):
        with patch(
            "api.agent.core.step_compaction.get_summarization_llm_config",
            return_value=("openai", "openai/test", {}),
        ), patch(
            "api.agent.core.step_compaction.run_completion",
            side_effect=OpenAIError("provider unavailable"),
        ):
            with self.assertRaises(CompactionSummaryError):
                llm_summarise_steps("", [], agent=self.agent)

    def test_unexpected_llm_error_is_not_marked_retryable(self):
        with patch(
            "api.agent.core.step_compaction.get_summarization_llm_config",
            return_value=("openai", "openai/test", {}),
        ), patch(
            "api.agent.core.step_compaction.run_completion",
            side_effect=RuntimeError("bug"),
        ):
            with self.assertRaisesRegex(RuntimeError, "bug"):
                llm_summarise_steps("", [], agent=self.agent)
