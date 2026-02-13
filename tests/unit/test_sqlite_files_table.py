import os
import sqlite3
import tempfile

from django.test import SimpleTestCase, tag

from api.agent.core.file_results import FileSQLiteRecord, store_files_for_prompt
from api.agent.tools.sqlite_state import reset_sqlite_db_path, set_sqlite_db_path


@tag("batch_sqlite")
class SqliteFilesTableTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "state.db")
        self.token = set_sqlite_db_path(self.db_path)

    def tearDown(self):
        reset_sqlite_db_path(self.token)
        self.tmp.cleanup()

    def test_store_files_for_prompt_creates_and_populates_table(self):
        records = [
            FileSQLiteRecord(
                node_id="node-1",
                filespace_id="fs-1",
                path="/exports/report.csv",
                name="report.csv",
                parent_path="/exports",
                mime_type="text/csv",
                size_bytes=123,
                checksum_sha256="abc123",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T01:00:00+00:00",
            )
        ]

        store_files_for_prompt(records)

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM "__files";')
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute(
                """
                SELECT node_id, filespace_id, path, parent_path, mime_type, size_bytes, checksum_sha256
                FROM "__files"
                WHERE node_id='node-1';
                """
            )
            row = cur.fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row[0], "node-1")
            self.assertEqual(row[1], "fs-1")
            self.assertEqual(row[2], "/exports/report.csv")
            self.assertEqual(row[3], "/exports")
            self.assertEqual(row[4], "text/csv")
            self.assertEqual(row[5], 123)
            self.assertEqual(row[6], "abc123")
            cur.execute('SELECT total_files, indexed_files, is_truncated FROM "__files_meta" WHERE id=1;')
            meta = cur.fetchone()
            self.assertEqual(meta, (1, 1, 0))
        finally:
            conn.close()

    def test_store_files_for_prompt_replaces_previous_snapshot(self):
        first = FileSQLiteRecord(
            node_id="node-old",
            filespace_id="fs-1",
            path="/old.txt",
            name="old.txt",
            parent_path="/",
            mime_type="text/plain",
            size_bytes=5,
            checksum_sha256="old",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        second = FileSQLiteRecord(
            node_id="node-new",
            filespace_id="fs-1",
            path="/new.txt",
            name="new.txt",
            parent_path="/",
            mime_type="text/plain",
            size_bytes=7,
            checksum_sha256="new",
            created_at="2026-01-02T00:00:00+00:00",
            updated_at="2026-01-02T00:00:00+00:00",
        )

        store_files_for_prompt([first])
        store_files_for_prompt([second])

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM "__files";')
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute('SELECT node_id FROM "__files";')
            self.assertEqual(cur.fetchone()[0], "node-new")
            cur.execute('SELECT total_files, indexed_files, is_truncated FROM "__files_meta" WHERE id=1;')
            meta = cur.fetchone()
            self.assertEqual(meta, (1, 1, 0))
        finally:
            conn.close()

    def test_store_files_for_prompt_writes_truncation_metadata(self):
        records = [
            FileSQLiteRecord(
                node_id="node-1",
                filespace_id="fs-1",
                path="/a.txt",
                name="a.txt",
                parent_path="/",
                mime_type="text/plain",
                size_bytes=1,
                checksum_sha256="x",
                created_at=None,
                updated_at=None,
            )
        ]
        store_files_for_prompt(
            records,
            total_files=50,
            indexed_files=1,
            is_truncated=True,
        )
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute('SELECT total_files, indexed_files, is_truncated FROM "__files_meta" WHERE id=1;')
            meta = cur.fetchone()
            self.assertEqual(meta, (50, 1, 1))
        finally:
            conn.close()
