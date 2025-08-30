"""
Tests for periodic task synchronization functionality.
"""
import unittest
from unittest.mock import patch, MagicMock
from django.test import TestCase, tag
from django.db.utils import ProgrammingError, OperationalError
from api.periodic_tasks import beat_schedule, add_dynamic_schedules, sync_to_redis, clean_up_old_decodo_schedules


@tag("batch_periodic")
class PeriodicTasksTest(TestCase):
    """Test periodic task schedule management."""

    def test_static_schedules_exist(self):
        """Test that static schedules dictionary exists and is properly structured."""
        # beat_schedule should be a dict (could be empty if no static tasks are needed)
        self.assertIsInstance(beat_schedule, dict)

    def test_add_dynamic_schedules(self):
        """Test that a single nightly sync schedule is added."""
        # Clear any existing schedules and add fresh ones
        beat_schedule.clear()
        
        add_dynamic_schedules()
        
        # Should add the single nightly sync schedule
        self.assertIn("decodo-ip-sync-daily", beat_schedule)
        self.assertEqual(beat_schedule["decodo-ip-sync-daily"]["task"], "gobii_platform.api.tasks.sync_all_ip_blocks")
        self.assertEqual(beat_schedule["decodo-ip-sync-daily"]["args"], [])
        
        # Should also add the nightly proxy health check schedule
        self.assertIn("proxy-health-check-nightly", beat_schedule)
        self.assertEqual(beat_schedule["proxy-health-check-nightly"]["task"], "gobii_platform.api.tasks.proxy_health_check_nightly")
        self.assertEqual(beat_schedule["proxy-health-check-nightly"]["args"], [])

        # Should also add the monthly prune schedule
        self.assertIn("prune-threshold-sent-monthly", beat_schedule)
        self.assertEqual(beat_schedule["prune-threshold-sent-monthly"]["task"], "prune_usage_threshold_sent")
        self.assertEqual(beat_schedule["prune-threshold-sent-monthly"]["args"], [])

    @patch('redis.from_url')
    def test_clean_up_old_decodo_schedules_handles_redis_error(self, mock_redis):
        """Test that cleanup_orphaned_schedules gracefully handles Redis connection errors."""
        # Simulate Redis connection error
        mock_redis.side_effect = Exception("Redis connection failed")
        
        # Should not raise an exception
        try:
            clean_up_old_decodo_schedules()
        except Exception as e:
            self.fail(f"cleanup_orphaned_schedules raised an exception when it shouldn't: {e}")

    @patch('api.periodic_tasks.RedBeatSchedulerEntry')
    @patch('api.periodic_tasks.clean_up_old_decodo_schedules')
    @patch('api.periodic_tasks.add_dynamic_schedules')
    def test_sync_to_redis(self, mock_add_dynamic, mock_cleanup, mock_entry):
        """Test that sync_to_redis calls appropriate functions."""
        sync_to_redis()
        
        mock_cleanup.assert_called_once()
        mock_add_dynamic.assert_called_once()
        # RedBeatSchedulerEntry should be called for each schedule
        self.assertTrue(mock_entry.called)
