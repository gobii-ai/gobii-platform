import sqlite3
from unittest.mock import patch

from django.test import SimpleTestCase, tag

import celery_task_counter


@tag("batch_browser_task_db")
class BrowserTaskCounterTests(SimpleTestCase):
    def test_increment_count_returns_zero_after_operational_errors(self):
        with patch(
            "celery_task_counter._get_conn",
            side_effect=sqlite3.OperationalError("busy"),
        ) as mock_conn, patch("celery_task_counter.time.sleep") as mock_sleep:
            result = celery_task_counter._increment_count("worker-1")

        self.assertEqual(result, 0)
        self.assertEqual(mock_conn.call_count, 5)
        self.assertEqual(mock_sleep.call_count, 5)
