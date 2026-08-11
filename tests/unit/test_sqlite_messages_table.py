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
                rejected_attachments=[],
                latest_status="queued",
                latest_sent_at=None,
                latest_delivered_at=None,
                latest_error_code=None,
                latest_error_message=None,
                is_hidden_in_chat=False,
                structured_payload_json='{"record_id":"rec-17","status":"ready"}',
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
                SELECT message_id, channel, is_outbound, direction, subject, attachment_paths_json,
                       attachment_count, rejected_attachments_json, structured_payload_json
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
            self.assertEqual(json.loads(row[7]), [])
            self.assertEqual(
                json.loads(row[8]),
                {"record_id": "rec-17", "status": "ready"},
            )
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
            rejected_attachments=[],
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
            rejected_attachments=[],
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

    def test_store_messages_for_prompt_does_not_rewrite_unchanged_rows(self):
        record = MessageSQLiteRecord(
            message_id="msg-stable",
            seq="S1",
            timestamp="2026-01-01T00:00:00+00:00",
            channel="web",
            is_outbound=False,
            from_address="web://user/1",
            to_address="web://agent/1",
            conversation_id="conv-1",
            conversation_address="web://user/1",
            is_peer_dm=False,
            peer_agent_id=None,
            subject="",
            body="unchanged",
            attachment_paths=[],
            rejected_attachments=[],
            latest_status="queued",
            latest_sent_at=None,
            latest_delivered_at=None,
            latest_error_code=None,
            latest_error_message=None,
            is_hidden_in_chat=False,
        )
        store_messages_for_prompt([record])

        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE message_change_audit (operation TEXT NOT NULL);
                CREATE TRIGGER audit_message_insert AFTER INSERT ON __messages
                BEGIN INSERT INTO message_change_audit VALUES ('insert'); END;
                CREATE TRIGGER audit_message_update AFTER UPDATE ON __messages
                BEGIN INSERT INTO message_change_audit VALUES ('update'); END;
                CREATE TRIGGER audit_message_delete AFTER DELETE ON __messages
                BEGIN INSERT INTO message_change_audit VALUES ('delete'); END;
                """
            )
            conn.commit()
        finally:
            conn.close()

        store_messages_for_prompt([record])

        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM message_change_audit").fetchone()[0],
                0,
            )
        finally:
            conn.close()

    def test_store_messages_for_prompt_updates_changed_row_and_deletes_absent_row(self):
        first = MessageSQLiteRecord(
            message_id="msg-change",
            seq="S1",
            timestamp="2026-01-01T00:00:00+00:00",
            channel="web",
            is_outbound=True,
            from_address="web://agent/1",
            to_address="web://user/1",
            conversation_id="conv-1",
            conversation_address="web://user/1",
            is_peer_dm=False,
            peer_agent_id=None,
            subject="",
            body="before",
            attachment_paths=[],
            rejected_attachments=[],
            latest_status="sent",
            latest_sent_at=None,
            latest_delivered_at=None,
            latest_error_code=None,
            latest_error_message=None,
            is_hidden_in_chat=False,
        )
        removed = MessageSQLiteRecord(
            **{
                **first.__dict__,
                "message_id": "msg-remove",
                "seq": "S2",
            }
        )
        changed = MessageSQLiteRecord(
            **{
                **first.__dict__,
                "body": "after",
                "latest_status": "delivered",
            }
        )
        store_messages_for_prompt([first, removed])

        store_messages_for_prompt([changed])

        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute(
                    'SELECT body, latest_status FROM "__messages" WHERE message_id=?',
                    ("msg-change",),
                ).fetchone(),
                ("after", "delivered"),
            )
            self.assertIsNone(
                conn.execute(
                    'SELECT 1 FROM "__messages" WHERE message_id=?',
                    ("msg-remove",),
                ).fetchone()
            )
        finally:
            conn.close()

    def test_store_messages_for_prompt_rebuilds_incompatible_table(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('CREATE TABLE "__messages" (message_id TEXT PRIMARY KEY)')
            conn.execute('INSERT INTO "__messages" VALUES (?)', ("legacy",))
            conn.commit()
        finally:
            conn.close()

        record = MessageSQLiteRecord(
            message_id="msg-current",
            seq="S1",
            timestamp="2026-01-01T00:00:00+00:00",
            channel="web",
            is_outbound=False,
            from_address="web://user/1",
            to_address="web://agent/1",
            conversation_id=None,
            conversation_address="",
            is_peer_dm=False,
            peer_agent_id=None,
            subject="",
            body="current",
            attachment_paths=[],
            rejected_attachments=[],
            latest_status="queued",
            latest_sent_at=None,
            latest_delivered_at=None,
            latest_error_code=None,
            latest_error_message=None,
            is_hidden_in_chat=False,
        )
        store_messages_for_prompt([record])

        conn = sqlite3.connect(self.db_path)
        try:
            columns = {
                row[1]
                for row in conn.execute('PRAGMA table_info("__messages")').fetchall()
            }
            self.assertIn("structured_payload_json", columns)
            self.assertEqual(
                conn.execute('SELECT message_id FROM "__messages"').fetchall(),
                [("msg-current",)],
            )
        finally:
            conn.close()

    def test_store_messages_for_prompt_rebuilds_table_without_message_id_primary_key(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE "__messages" (
                    message_id TEXT,
                    seq TEXT,
                    timestamp TEXT,
                    channel TEXT,
                    is_outbound INTEGER,
                    direction TEXT,
                    from_address TEXT,
                    to_address TEXT,
                    conversation_id TEXT,
                    conversation_address TEXT,
                    is_peer_dm INTEGER,
                    peer_agent_id TEXT,
                    subject TEXT,
                    body TEXT,
                    body_bytes INTEGER,
                    body_is_truncated INTEGER,
                    body_truncated_bytes INTEGER,
                    attachment_paths_json TEXT,
                    attachment_count INTEGER,
                    rejected_attachments_json TEXT,
                    latest_status TEXT,
                    latest_sent_at TEXT,
                    latest_delivered_at TEXT,
                    latest_error_code TEXT,
                    latest_error_message TEXT,
                    is_hidden_in_chat INTEGER,
                    structured_payload_json TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        record = MessageSQLiteRecord(
            message_id="msg-current",
            seq="S1",
            timestamp="2026-01-01T00:00:00+00:00",
            channel="web",
            is_outbound=False,
            from_address="web://user/1",
            to_address="web://agent/1",
            conversation_id=None,
            conversation_address="web://user/1",
            is_peer_dm=False,
            peer_agent_id=None,
            subject="",
            body="current",
            attachment_paths=[],
            rejected_attachments=[],
            latest_status="",
            latest_sent_at=None,
            latest_delivered_at=None,
            latest_error_code=None,
            latest_error_message=None,
            is_hidden_in_chat=False,
        )

        store_messages_for_prompt([record])

        conn = sqlite3.connect(self.db_path)
        try:
            table_info = conn.execute('PRAGMA table_info("__messages")').fetchall()
            message_id_info = next(row for row in table_info if row[1] == "message_id")
            self.assertEqual(message_id_info[5], 1)
            self.assertEqual(
                conn.execute('SELECT message_id FROM "__messages"').fetchone()[0],
                "msg-current",
            )
        finally:
            conn.close()

    def test_store_messages_for_prompt_empty_snapshot_clears_table(self):
        record = MessageSQLiteRecord(
            message_id="msg-clear",
            seq="S1",
            timestamp="2026-01-01T00:00:00+00:00",
            channel="web",
            is_outbound=False,
            from_address="web://user/1",
            to_address="web://agent/1",
            conversation_id=None,
            conversation_address="",
            is_peer_dm=False,
            peer_agent_id=None,
            subject="",
            body="clear me",
            attachment_paths=[],
            rejected_attachments=[],
            latest_status="queued",
            latest_sent_at=None,
            latest_delivered_at=None,
            latest_error_code=None,
            latest_error_message=None,
            is_hidden_in_chat=False,
        )
        store_messages_for_prompt([record])

        store_messages_for_prompt([])

        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute('SELECT COUNT(*) FROM "__messages"').fetchone()[0],
                0,
            )
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
            rejected_attachments=[
                {"filename": "deck.pdf", "reason_code": "too_large", "limit_bytes": 1024, "channel": "email"}
            ],
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
                       latest_sent_at, latest_delivered_at, latest_error_code, latest_error_message, rejected_attachments_json
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
            self.assertEqual(
                json.loads(row[9]),
                [{"filename": "deck.pdf", "reason_code": "too_large", "limit_bytes": 1024, "channel": "email"}],
            )
        finally:
            conn.close()
