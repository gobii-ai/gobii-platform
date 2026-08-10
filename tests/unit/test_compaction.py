import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings, tag
from django.utils import timezone
from django.contrib.auth import get_user_model
from openai import OpenAIError

from api.agent.core.compaction import ensure_comms_compacted, llm_summarise_comms
from api.agent.core.compaction_exceptions import CompactionSummaryError
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PersistentAgentCommsSnapshot,
)

User = get_user_model()


@override_settings(PA_RAW_MSG_LIMIT=10, PA_COMMS_COMPACTION_TAIL=2)
@tag("batch_compaction")
class CompactionTests(TestCase):
    """Unit-tests for on-demand message history compaction."""

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
        self.endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel="email",
            address="tester@example.com",
        )

    def _make_message(self, ts):
        # Generate a deterministic but unique 26-char ULID-like string
        seq = f"TEST{int(ts.timestamp() * 1_000_000):022d}"[:26]

        return PersistentAgentMessage.objects.create(
            timestamp=ts,
            seq=seq,
            from_endpoint=self.endpoint,
            to_endpoint=self.endpoint,
            is_outbound=False,
            owner_agent=self.agent,
            body="test msg",
        )

    def _make_named_message(self, ts, suffix, body):
        message = PersistentAgentMessage.objects.create(
            seq=f"MSG{suffix:023d}"[:26],
            from_endpoint=self.endpoint,
            to_endpoint=self.endpoint,
            is_outbound=False,
            owner_agent=self.agent,
            body=body,
        )
        PersistentAgentMessage.objects.filter(id=message.id).update(timestamp=ts)
        message.timestamp = ts
        return message

    def _compact(self):
        def summarize(previous, messages, safety_identifier):
            return (
                previous
                + ("\n" if previous else "")
                + f"[SUMMARY PLACEHOLDER for {len(messages)} messages]\n"
                + (f"[Called for {safety_identifier}]" if safety_identifier else "")
            )

        ensure_comms_compacted(agent=self.agent, summarise_fn=summarize)

    @tag("batch_compaction")
    def test_compaction_triggered_when_over_limit(self):
        """When raw messages > limit, a new snapshot is created."""
        # Sanity-check: no snapshots at start
        self.assertEqual(PersistentAgentCommsSnapshot.objects.count(), 0)

        # Create one more message than the raw limit
        num_messages = settings.PA_RAW_MSG_LIMIT + 1
        for i in range(num_messages):
            self._make_message(self.agent.created_at + timedelta(seconds=i + 1))

        # Run compaction
        self._compact()

        # A snapshot should have been created
        self.assertEqual(PersistentAgentCommsSnapshot.objects.count(), 1)
        snapshot = PersistentAgentCommsSnapshot.objects.first()

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.agent, self.agent)
        self.assertIsNone(snapshot.previous_snapshot)
        compacted_count = num_messages - settings.PA_COMMS_COMPACTION_TAIL
        self.assertIn(
            f"[SUMMARY PLACEHOLDER for {compacted_count} messages]", snapshot.summary
        )

        # Check snapshot_until is correct (timestamp of the last compacted message)
        ordered = list(PersistentAgentMessage.objects.order_by("timestamp"))
        expected_until = ordered[-(settings.PA_COMMS_COMPACTION_TAIL + 1)].timestamp
        self.assertEqual(snapshot.snapshot_until, expected_until)

        # Tail messages should remain after the snapshot cutoff
        remaining = PersistentAgentMessage.objects.filter(
            timestamp__gt=snapshot.snapshot_until
        ).count()
        self.assertEqual(remaining, settings.PA_COMMS_COMPACTION_TAIL)

    @tag("batch_compaction")
    def test_no_compaction_when_at_or_below_limit(self):
        """No snapshot should be created when raw messages <= limit."""
        # Create up to the limit (should not compact)
        for i in range(settings.PA_RAW_MSG_LIMIT):
            self._make_message(self.agent.created_at + timedelta(seconds=i + 1))

        # Run compaction
        self._compact()

        # Still no snapshots expected
        self.assertEqual(PersistentAgentCommsSnapshot.objects.count(), 0)

    @tag("batch_compaction")
    def test_incremental_compaction_with_existing_snapshot(self):
        """A second compaction should create a new snapshot linked to the previous one."""
        # ------------------- First batch ------------------- #
        first_batch = settings.PA_RAW_MSG_LIMIT + 1
        for i in range(first_batch):
            self._make_message(self.agent.created_at + timedelta(seconds=i + 1))

        self._compact()
        self.assertEqual(PersistentAgentCommsSnapshot.objects.count(), 1)
        first_snapshot = PersistentAgentCommsSnapshot.objects.first()
        self.assertIsNotNone(first_snapshot)

        # ------------------ Second batch ------------------ #
        second_batch = settings.PA_RAW_MSG_LIMIT + 1
        start_sec = first_batch + 1
        for i in range(second_batch):
            self._make_message(self.agent.created_at + timedelta(seconds=start_sec + i))

        self._compact()

        # We should now have exactly two snapshots.
        self.assertEqual(PersistentAgentCommsSnapshot.objects.count(), 2)
        latest_snapshot = PersistentAgentCommsSnapshot.objects.order_by("-snapshot_until").first()
        self.assertIsNotNone(latest_snapshot)
        self.assertEqual(latest_snapshot.previous_snapshot, first_snapshot)

        # Summary should include both the previous snapshot's content and the new placeholder.
        self.assertIn(first_snapshot.summary, latest_snapshot.summary)
        expected_compacted = (
            PersistentAgentMessage.objects.filter(
                timestamp__gt=first_snapshot.snapshot_until
            ).count()
            - settings.PA_COMMS_COMPACTION_TAIL
        )
        self.assertIn(
            f"[SUMMARY PLACEHOLDER for {expected_compacted} messages]", latest_snapshot.summary
        )

        # snapshot_until should correspond to the last compacted message
        ordered = list(PersistentAgentMessage.objects.order_by("timestamp"))
        expected_until = ordered[-(settings.PA_COMMS_COMPACTION_TAIL + 1)].timestamp
        self.assertEqual(latest_snapshot.snapshot_until, expected_until)

    @override_settings(PA_RAW_MSG_LIMIT=4, PA_COMMS_COMPACTION_TAIL=2)
    def test_cutoff_includes_every_message_with_the_same_timestamp(self):
        base_time = self.agent.created_at
        shared_time = base_time + timedelta(seconds=3)
        messages = [
            self._make_named_message(base_time + timedelta(seconds=1), 1, "one"),
            self._make_named_message(base_time + timedelta(seconds=2), 2, "two"),
            self._make_named_message(shared_time, 3, "three"),
            self._make_named_message(shared_time, 4, "four"),
            self._make_named_message(base_time + timedelta(seconds=4), 5, "five"),
        ]
        summarized_ids = []

        def summarize(_previous, selected, _safety_identifier):
            summarized_ids.extend(str(message.id) for message in selected)
            return "summary"

        ensure_comms_compacted(agent=self.agent, summarise_fn=summarize)

        snapshot = PersistentAgentCommsSnapshot.objects.get()
        self.assertEqual(snapshot.snapshot_until, shared_time)
        self.assertCountEqual(
            summarized_ids,
            [str(message.id) for message in messages[:4]],
        )
        self.assertEqual(
            list(
                PersistentAgentMessage.objects.filter(
                    timestamp__gt=snapshot.snapshot_until
                ).values_list("body", flat=True)
            ),
            ["five"],
        )

    @override_settings(PA_RAW_MSG_LIMIT=3, PA_COMMS_COMPACTION_TAIL=1)
    def test_backdated_message_during_summary_aborts_snapshot(self):
        base_time = self.agent.created_at
        for index in range(4):
            self._make_named_message(
                base_time + timedelta(seconds=index + 1),
                index + 1,
                f"original {index}",
            )

        def summarize(_previous, selected, _safety_identifier):
            self._make_named_message(selected[-1].timestamp, 99, "arrived during summary")
            return "summary"

        ensure_comms_compacted(agent=self.agent, summarise_fn=summarize)

        self.assertFalse(PersistentAgentCommsSnapshot.objects.exists())
        self.assertEqual(PersistentAgentMessage.objects.count(), 5)

    @override_settings(PA_RAW_MSG_LIMIT=3, PA_COMMS_COMPACTION_TAIL=1)
    def test_message_arriving_after_cutoff_remains_raw(self):
        base_time = self.agent.created_at
        for index in range(4):
            self._make_named_message(
                base_time + timedelta(seconds=index + 1),
                index + 1,
                f"original {index}",
            )

        def summarize(_previous, selected, _safety_identifier):
            self._make_named_message(
                selected[-1].timestamp + timedelta(seconds=10),
                99,
                "arrived after cutoff",
            )
            return "summary"

        ensure_comms_compacted(agent=self.agent, summarise_fn=summarize)

        snapshot = PersistentAgentCommsSnapshot.objects.get()
        self.assertCountEqual(
            list(
                PersistentAgentMessage.objects.filter(
                    timestamp__gt=snapshot.snapshot_until,
                ).values_list("body", flat=True)
            ),
            ["original 3", "arrived after cutoff"],
        )

    @override_settings(PA_RAW_MSG_LIMIT=3, PA_COMMS_COMPACTION_TAIL=1)
    def test_empty_summary_does_not_advance_snapshot(self):
        base_time = self.agent.created_at
        for index in range(4):
            self._make_named_message(
                base_time + timedelta(seconds=index + 1),
                index + 1,
                f"message {index}",
            )

        with self.assertRaises(CompactionSummaryError):
            ensure_comms_compacted(
                agent=self.agent,
                summarise_fn=lambda _previous, _messages, _safety_identifier: "",
            )

        self.assertFalse(PersistentAgentCommsSnapshot.objects.exists())

    @override_settings(PA_RAW_MSG_LIMIT=3, PA_COMMS_COMPACTION_TAIL=1)
    def test_competing_compaction_keeps_single_complete_snapshot(self):
        base_time = self.agent.created_at
        for index in range(4):
            self._make_named_message(
                base_time + timedelta(seconds=index + 1),
                index + 1,
                f"message {index}",
            )

        def summarize_after_competitor(_previous, _messages, _safety_identifier):
            ensure_comms_compacted(
                agent=self.agent,
                summarise_fn=lambda _prior, selected, _safety: (
                    "inner:" + ",".join(message.body for message in selected)
                ),
            )
            return "outer summary should lose the race"

        ensure_comms_compacted(
            agent=self.agent,
            summarise_fn=summarize_after_competitor,
        )

        self.assertEqual(PersistentAgentCommsSnapshot.objects.count(), 1)
        snapshot = PersistentAgentCommsSnapshot.objects.get()
        self.assertTrue(snapshot.summary.startswith("inner:"))
        self.assertNotIn("outer summary", snapshot.summary)

    def test_llm_compaction_truncates_body_and_payload_independently(self):
        payload = {
            "records": [
                {"id": index, "value": "x" * 200}
                for index in range(40)
            ]
        }
        message = SimpleNamespace(
            is_outbound=False,
            body="b" * 5000,
            raw_payload={
                "_source": "agent_peer_dm",
                "structured_payload": payload,
            },
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))]
        )

        with patch(
            "api.agent.core.compaction.get_summarization_llm_config",
            return_value=("openai", "openai/test", {}),
        ), patch(
            "api.agent.core.compaction.run_completion",
            return_value=response,
        ) as run_completion_mock, patch(
            "api.agent.core.compaction.log_agent_completion",
            return_value=(None, {}),
        ), patch("api.agent.core.compaction.set_usage_span_attributes"):
            summary = llm_summarise_comms("", [message])

        self.assertEqual(summary, "summary")
        prompt = run_completion_mock.call_args.kwargs["messages"][1]["content"]
        body_preview, payload_block = (
            prompt.split("New messages:\nInbound message from unknown sender: ", 1)[1]
            .split("\n\nRewrite the state", 1)[0]
            .split("\nStructured payload:\n", 1)
        )
        payload_preview = json.loads(payload_block)

        self.assertEqual(len(body_preview), 4000)
        self.assertLessEqual(len(payload_block), 4000)
        self.assertTrue(payload_preview["_compaction_truncated"])
        self.assertGreater(payload_preview["_original_char_count"], 4000)

    def test_llm_failure_raises_retryable_compaction_error(self):
        with patch(
            "api.agent.core.compaction.get_summarization_llm_config",
            return_value=("openai", "openai/test", {}),
        ), patch(
            "api.agent.core.compaction.run_completion",
            side_effect=OpenAIError("provider unavailable"),
        ):
            with self.assertRaises(CompactionSummaryError):
                llm_summarise_comms("", [], agent=self.agent)

    def test_unexpected_llm_error_is_not_marked_retryable(self):
        with patch(
            "api.agent.core.compaction.get_summarization_llm_config",
            return_value=("openai", "openai/test", {}),
        ), patch(
            "api.agent.core.compaction.run_completion",
            side_effect=RuntimeError("bug"),
        ):
            with self.assertRaisesRegex(RuntimeError, "bug"):
                llm_summarise_comms("", [], agent=self.agent)

    def test_llm_compaction_preserves_actor_and_channel_identity(self):
        messages = [
            SimpleNamespace(
                is_outbound=False,
                body="The rollout risk is billing reconciliation, not traffic volume.",
                raw_payload={
                    "source_kind": "discord",
                    "source_label": "Will in #growth",
                },
                from_endpoint=SimpleNamespace(channel="discord", address="discord://growth"),
                to_endpoint=None,
                conversation=None,
                peer_agent=None,
            ),
            SimpleNamespace(
                is_outbound=True,
                body="I will verify the billing cohort before the release call.",
                raw_payload={},
                from_endpoint=SimpleNamespace(channel="email", address="agent@example.test"),
                to_endpoint=SimpleNamespace(channel="email", address="owner@example.test"),
                conversation=None,
                peer_agent=None,
            ),
        ]
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))]
        )

        with patch(
            "api.agent.core.compaction.get_summarization_llm_config",
            return_value=("openai", "openai/test", {}),
        ), patch(
            "api.agent.core.compaction.run_completion",
            return_value=response,
        ) as run_completion_mock, patch(
            "api.agent.core.compaction.log_agent_completion",
            return_value=(None, {}),
        ), patch("api.agent.core.compaction.set_usage_span_attributes"):
            summary = llm_summarise_comms("", messages)

        self.assertEqual(summary, "summary")
        messages = run_completion_mock.call_args.kwargs["messages"]
        system_prompt = messages[0]["content"]
        user_prompt = messages[1]["content"]
        self.assertIn("Inbound discord from Will in #growth:", user_prompt)
        self.assertIn("Outbound email to owner@example.test:", user_prompt)
        self.assertIn("Replace superseded state", system_prompt)
        self.assertIn("never mention replaced values", system_prompt)
        self.assertIn("collapse closed batches to counts", system_prompt)
        self.assertIn("exact identifiers", system_prompt)
        self.assertIn("Copy opaque values", system_prompt)
        self.assertIn("never alter their case, punctuation, spacing, or characters", system_prompt)
        self.assertIn("unresolved work", system_prompt)
        self.assertIn("still-operative scoped directives", system_prompt)
        self.assertIn("stop/do-not-act instructions", system_prompt)
        self.assertIn("actor, source, scope identifier, and effective constraint", system_prompt)
        self.assertIn("until explicitly superseded, expired, or reassigned", system_prompt)
        self.assertIn("no continuing consequence", system_prompt)
        self.assertIn("never weaken forbidden work", system_prompt)
        self.assertIn("must be at most 2,000 characters", system_prompt)
        self.assertIn("silently audit the draft", system_prompt)
        self.assertIn("delete every superseded value", system_prompt)
        self.assertIn("compare every opaque value character-for-character", system_prompt)
        self.assertIn("sent, delivered, and confirmed are not interchangeable", system_prompt)
