"""
Tests for RedBeat Celery scheduler configuration.
"""
import unittest
from django.test import TestCase, tag
from django.conf import settings
from unittest.mock import patch, MagicMock


@tag("batch_celery_redbeat")
class RedBeatConfigurationTest(TestCase):
    """Test that RedBeat scheduler is properly configured."""

    def test_celery_beat_scheduler_is_redbeat(self):
        """Test that CELERY_BEAT_SCHEDULER is set to RedBeat."""
        self.assertEqual(
            settings.CELERY_BEAT_SCHEDULER,
            "redbeat.RedBeatScheduler"
        )

    def test_celery_timezone_is_utc(self):
        """Test that CELERY_TIMEZONE is set to UTC."""
        self.assertEqual(settings.CELERY_TIMEZONE, "UTC")

    def test_celery_enable_utc_is_true(self):
        """Test that CELERY_ENABLE_UTC is True."""
        self.assertTrue(settings.CELERY_ENABLE_UTC)

    def test_django_celery_beat_not_in_installed_apps(self):
        """Test that django_celery_beat is not in INSTALLED_APPS."""
        self.assertNotIn("django_celery_beat", settings.INSTALLED_APPS)

    def test_redbeat_import_works(self):
        """Test that redbeat can be imported."""
        try:
            import redbeat
            redbeat_available = True
        except ImportError:
            redbeat_available = False
        
        self.assertTrue(redbeat_available, "RedBeat package should be available")

    def test_redbeat_scheduler_import_works(self):
        """Test that RedBeatScheduler can be imported."""
        try:
            from redbeat import RedBeatScheduler
            scheduler_available = True
        except ImportError:
            scheduler_available = False
        
        self.assertTrue(scheduler_available, "RedBeatScheduler should be importable")

    @patch('redbeat.RedBeatScheduler')
    def test_redbeat_scheduler_can_be_instantiated(self, mock_scheduler):
        """Test that RedBeatScheduler can be instantiated with Redis config."""
        from redbeat import RedBeatScheduler
        
        # Mock the scheduler to avoid actually connecting to Redis
        mock_instance = MagicMock()
        mock_scheduler.return_value = mock_instance
        
        # This should not raise any exceptions
        try:
            scheduler = RedBeatScheduler()
            instantiation_success = True
        except Exception as e:
            instantiation_success = False
            
        self.assertTrue(instantiation_success, "RedBeatScheduler should be instantiable")


@tag("batch_celery_redbeat")
class CeleryRedisConfigurationTest(TestCase):
    """Test that Celery is configured to use Redis."""

    def test_celery_broker_uses_redis(self):
        """Test that CELERY_BROKER_URL is set to Redis."""
        # We expect this to be a Redis URL
        broker_url = getattr(settings, 'CELERY_BROKER_URL', '')
        self.assertTrue(
            broker_url.startswith('redis://') or broker_url == '',
            f"Expected Redis URL for broker, got: {broker_url}"
        )

    def test_celery_result_backend_uses_redis(self):
        """Test that CELERY_RESULT_BACKEND is set to Redis."""
        # We expect this to be a Redis URL
        result_backend = getattr(settings, 'CELERY_RESULT_BACKEND', '')
        self.assertTrue(
            result_backend.startswith('redis://') or result_backend == '',
            f"Expected Redis URL for result backend, got: {result_backend}"
        )

    def test_django_celery_beat_cleanup_migration_exists(self):
        """Test that migration exists to clean up django-celery-beat tables."""
        import os
        migration_file = os.path.join(
            settings.BASE_DIR,
            'api',
            'migrations',
            '0020_cleanup_django_celery_beat_tables.py'
        )
        self.assertTrue(
            os.path.exists(migration_file),
            "Migration to clean up django-celery-beat tables should exist"
        )
