from datetime import timedelta
from django.test import TestCase, override_settings, tag
from django.utils import timezone
from django.contrib.auth import get_user_model

from api.agent.core.compaction import ensure_comms_compacted
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

    @tag("batch_compaction")
    def test_compaction_triggered_when_over_limit(self):
        """When raw messages > limit, a new snapshot is created."""
        from api.agent.core.compaction import RAW_MSG_LIMIT
        from api.agent.core.compaction import COMMS_COMPACTION_TAIL

        # Sanity-check: no snapshots at start
        self.assertEqual(PersistentAgentCommsSnapshot.objects.count(), 0)

        # Create one more message than the limit + tail
        num_messages = RAW_MSG_LIMIT + COMMS_COMPACTION_TAIL + 1
        for i in range(num_messages):
            self._make_message(self.agent.created_at + timedelta(seconds=i + 1))

        # Run compaction
        ensure_comms_compacted(agent=self.agent)

        # A snapshot should have been created
        self.assertEqual(PersistentAgentCommsSnapshot.objects.count(), 1)
        snapshot = PersistentAgentCommsSnapshot.objects.first()

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.agent, self.agent)
        self.assertIsNone(snapshot.previous_snapshot)
        compacted_count = num_messages - COMMS_COMPACTION_TAIL
        self.assertIn(
            f"[SUMMARY PLACEHOLDER for {compacted_count} messages]", snapshot.summary
        )

        # Check snapshot_until is correct (timestamp of the last compacted message)
        ordered = list(PersistentAgentMessage.objects.order_by("timestamp"))
        expected_until = ordered[-(COMMS_COMPACTION_TAIL + 1)].timestamp
        self.assertEqual(snapshot.snapshot_until, expected_until)

        # Tail messages should remain after the snapshot cutoff
        remaining = PersistentAgentMessage.objects.filter(
            timestamp__gt=snapshot.snapshot_until
        ).count()
        self.assertEqual(remaining, COMMS_COMPACTION_TAIL)

    @tag("batch_compaction")
    def test_no_compaction_when_at_or_below_limit(self):
        """No snapshot should be created when raw messages <= limit."""
        from api.agent.core.compaction import RAW_MSG_LIMIT
        from api.agent.core.compaction import COMMS_COMPACTION_TAIL

        # Create up to the limit + tail (should not compact)
        for i in range(RAW_MSG_LIMIT + COMMS_COMPACTION_TAIL):
            self._make_message(self.agent.created_at + timedelta(seconds=i + 1))

        # Run compaction
        ensure_comms_compacted(agent=self.agent)

        # Still no snapshots expected
        self.assertEqual(PersistentAgentCommsSnapshot.objects.count(), 0)

    @tag("batch_compaction")
    def test_incremental_compaction_with_existing_snapshot(self):
        """A second compaction should create a new snapshot linked to the previous one."""
        from api.agent.core.compaction import RAW_MSG_LIMIT
        from api.agent.core.compaction import COMMS_COMPACTION_TAIL

        # ------------------- First batch ------------------- #
        first_batch = RAW_MSG_LIMIT + COMMS_COMPACTION_TAIL + 1
        for i in range(first_batch):
            self._make_message(self.agent.created_at + timedelta(seconds=i + 1))

        ensure_comms_compacted(agent=self.agent)
        self.assertEqual(PersistentAgentCommsSnapshot.objects.count(), 1)
        first_snapshot = PersistentAgentCommsSnapshot.objects.first()
        self.assertIsNotNone(first_snapshot)

        # ------------------ Second batch ------------------ #
        second_batch = RAW_MSG_LIMIT + COMMS_COMPACTION_TAIL + 2  # different size to distinguish
        start_sec = first_batch + 1
        for i in range(second_batch):
            self._make_message(self.agent.created_at + timedelta(seconds=start_sec + i))

        ensure_comms_compacted(agent=self.agent)

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
            - COMMS_COMPACTION_TAIL
        )
        self.assertIn(
            f"[SUMMARY PLACEHOLDER for {expected_compacted} messages]", latest_snapshot.summary
        )

        # snapshot_until should correspond to the last compacted message
        ordered = list(PersistentAgentMessage.objects.order_by("timestamp"))
        expected_until = ordered[-(COMMS_COMPACTION_TAIL + 1)].timestamp
        self.assertEqual(latest_snapshot.snapshot_until, expected_until) 
