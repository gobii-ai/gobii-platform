"""
Tests for the sync_schedules management command.
"""
from unittest.mock import patch
from django.test import TestCase, tag
from django.core.management import call_command
from io import StringIO


@tag("batch_periodic")
class SyncSchedulesCommandTest(TestCase):
    """Test the sync_schedules management command."""

    @patch('api.management.commands.sync_schedules.sync_to_redis')
    def test_sync_schedules_command(self, mock_sync):
        """Test that the command calls sync_to_redis."""
        out = StringIO()
        call_command('sync_schedules', stdout=out)
        
        mock_sync.assert_called_once()
        self.assertIn("schedules synced to Redis", out.getvalue())
