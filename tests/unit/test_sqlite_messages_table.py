import json
import os
import sqlite3
import tempfile

from django.test import SimpleTestCase, tag

from api.agent.core.message_results import MessageSQLiteRecord, store_messages_for_prompt
from api.agent.tools.sqlite_state import reset_sqlite_db_path, set_sqlite_db_path


@tag("batch_sqlite")
class SqliteMessagesTableTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "state.db")
        self.token = set_sqlite_db_path(self.db_path)

    def tearDown(self):
        reset_sqlite_db_path(self.token)
        self.tmp.cleanup()

    def test_store_messages_for_prompt_creates_and_populates_table(self):
        records = [
            MessageSQLiteRecord(
                message_id="msg-1",
                seq="S1",
                timestamp="2026-01-01T00:00:00+00:00",
                channel="email",
                is_outbound=False,
                from_address="user@example.com",
                to_address="agent@example.com",
                conversation_id="conv-1",
                conversation_address="user@example.com",
                is_peer_dm=False,
                peer_agent_id=None,
                subject="Hello",
                body="Need an update",
                attachment_paths=["/reports/daily.csv"],
                latest_status="queued",
                latest_sent_at=None,
                latest_delivered_at=None,
                latest_error_code=None,
                latest_error_message=None,
                is_hidden_in_chat=False,
            )
        ]

        store_messages_for_prompt(records)

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM "__messages";')
            self.assertEqual(cur.fetchone()[0], 1)

            cur.execute(
                """
                SELECT message_id, channel, is_outbound, direction, subject, attachment_paths_json, attachment_count
                FROM "__messages"
                WHERE message_id='msg-1';
                """
            )
            row = cur.fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row[0], "msg-1")
            self.assertEqual(row[1], "email")
            self.assertEqual(row[2], 0)
            self.assertEqual(row[3], "inbound")
            self.assertEqual(row[4], "Hello")
            self.assertEqual(json.loads(row[5]), ["/reports/daily.csv"])
            self.assertEqual(row[6], 1)
        finally:
            conn.close()

    def test_store_messages_for_prompt_replaces_previous_snapshot(self):
        first = MessageSQLiteRecord(
            message_id="msg-old",
            seq="S1",
            timestamp="2026-01-01T00:00:00+00:00",
            channel="sms",
            is_outbound=True,
            from_address="+15550000000",
            to_address="+15551111111",
            conversation_id=None,
            conversation_address="",
            is_peer_dm=False,
            peer_agent_id=None,
            subject="",
            body="first",
            attachment_paths=[],
            latest_status="sent",
            latest_sent_at="2026-01-01T00:00:01+00:00",
            latest_delivered_at=None,
            latest_error_code=None,
            latest_error_message=None,
            is_hidden_in_chat=False,
        )
        second = MessageSQLiteRecord(
            message_id="msg-new",
            seq="S2",
            timestamp="2026-01-02T00:00:00+00:00",
            channel="web",
            is_outbound=True,
            from_address="web://agent/1",
            to_address="web://user/1/agent/1",
            conversation_id="conv-2",
            conversation_address="web://user/1/agent/1",
            is_peer_dm=False,
            peer_agent_id=None,
            subject="",
            body="second",
            attachment_paths=[],
            latest_status="delivered",
            latest_sent_at="2026-01-02T00:00:01+00:00",
            latest_delivered_at="2026-01-02T00:00:01+00:00",
            latest_error_code=None,
            latest_error_message=None,
            is_hidden_in_chat=False,
        )

        store_messages_for_prompt([first])
        store_messages_for_prompt([second])

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM "__messages";')
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute('SELECT message_id FROM "__messages";')
            self.assertEqual(cur.fetchone()[0], "msg-new")
        finally:
            conn.close()

    def test_store_messages_for_prompt_uses_null_for_optional_fields_and_full_body(self):
        record = MessageSQLiteRecord(
            message_id="msg-trunc",
            seq="S3",
            timestamp="2026-01-03T00:00:00+00:00",
            channel="email",
            is_outbound=True,
            from_address="agent@example.com",
            to_address="user@example.com",
            conversation_id=None,
            conversation_address="",
            is_peer_dm=False,
            peer_agent_id=None,
            subject="",
            body="ééé",
            attachment_paths=[],
            latest_status="failed",
            latest_sent_at=None,
            latest_delivered_at=None,
            latest_error_code="",
            latest_error_message=" ",
            is_hidden_in_chat=False,
        )

        store_messages_for_prompt([record])

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT body, body_is_truncated, body_truncated_bytes, conversation_id, peer_agent_id,
                       latest_sent_at, latest_delivered_at, latest_error_code, latest_error_message
                FROM "__messages"
                WHERE message_id='msg-trunc';
                """
            )
            row = cur.fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row[0], "ééé")
            self.assertEqual(row[1], 0)
            self.assertEqual(row[2], 0)
            self.assertIsNone(row[3])
            self.assertIsNone(row[4])
            self.assertIsNone(row[5])
            self.assertIsNone(row[6])
            self.assertIsNone(row[7])
            self.assertIsNone(row[8])
        finally:
            conn.close()
