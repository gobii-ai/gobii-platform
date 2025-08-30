"""
Tests for Chrome profile pruning functionality in browser agent tasks.
"""
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from django.test import TestCase, tag

from api.tasks.browser_agent_tasks import _prune_chrome_profile, CHROME_PROFILE_MAX_SIZE_BYTES


@tag("batch_browser_profile")
class ChromeProfilePruningTest(TestCase):
    """Test the Chrome profile pruning logic."""

    def setUp(self):
        """Create a temporary directory to simulate a Chrome profile."""
        self.test_profile_dir = tempfile.mkdtemp(prefix="test_chrome_profile_")
        
    def tearDown(self):
        """Clean up the temporary directory."""
        if os.path.exists(self.test_profile_dir):
            shutil.rmtree(self.test_profile_dir)

    def _create_test_file(self, rel_path: str, size_bytes: int = 1024) -> str:
        """Create a test file with specified size."""
        full_path = os.path.join(self.test_profile_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        with open(full_path, 'wb') as f:
            f.write(b'x' * size_bytes)
        
        return full_path

    def _create_test_dir(self, rel_path: str) -> str:
        """Create a test directory."""
        full_path = os.path.join(self.test_profile_dir, rel_path)
        os.makedirs(full_path, exist_ok=True)
        return full_path

    def _get_dir_size(self, path: str) -> int:
        """Calculate total size of directory."""
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(path):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                try:
                    total_size += os.path.getsize(file_path)
                except FileNotFoundError:
                    pass
        return total_size

    @tag("batch_browser_profile")
    @patch('api.tasks.browser_agent_tasks.logger')
    def test_prune_removes_cache_directories(self, mock_logger):
        """Test that cache directories are removed during pruning."""
        # Create cache directories that should be removed
        self._create_test_dir("Cache")
        self._create_test_file("Cache/data.bin", 2048)
        self._create_test_dir("Code Cache")
        self._create_test_file("Code Cache/script.js", 1024)
        self._create_test_dir("GPUCache")
        self._create_test_file("GPUCache/shader.bin", 512)
        
        # Create a file that should NOT be removed
        self._create_test_file("Preferences", 256)
        
        # Verify directories exist before pruning
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "Cache")))
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "Code Cache")))
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "GPUCache")))
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "Preferences")))
        
        # Run pruning
        _prune_chrome_profile(self.test_profile_dir)
        
        # Verify cache directories are removed
        self.assertFalse(os.path.exists(os.path.join(self.test_profile_dir, "Cache")))
        self.assertFalse(os.path.exists(os.path.join(self.test_profile_dir, "Code Cache")))
        self.assertFalse(os.path.exists(os.path.join(self.test_profile_dir, "GPUCache")))
        
        # Verify important files remain
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "Preferences")))
        
        # Verify logging occurred
        mock_logger.info.assert_called()

    @tag("batch_browser_profile")
    @patch('api.tasks.browser_agent_tasks.logger')
    def test_prune_removes_temp_files(self, mock_logger):
        """Test that temporary files are removed during pruning."""
        # Create temporary files that should be removed
        self._create_test_file("temp_file.tmp", 512)
        self._create_test_file("backup.old", 1024)
        self._create_test_file("BrowserMetrics-spare.pma", 256)
        
        # Create files that should NOT be removed
        self._create_test_file("important.json", 128)
        self._create_test_file("data.log", 64)
        
        # Verify files exist before pruning
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "temp_file.tmp")))
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "backup.old")))
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "BrowserMetrics-spare.pma")))
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "important.json")))
        
        # Run pruning
        _prune_chrome_profile(self.test_profile_dir)
        
        # Verify temp files are removed
        self.assertFalse(os.path.exists(os.path.join(self.test_profile_dir, "temp_file.tmp")))
        self.assertFalse(os.path.exists(os.path.join(self.test_profile_dir, "backup.old")))
        self.assertFalse(os.path.exists(os.path.join(self.test_profile_dir, "BrowserMetrics-spare.pma")))
        
        # Verify important files remain
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "important.json")))
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "data.log")))

    @patch('api.tasks.browser_agent_tasks.logger')
    def test_prune_service_worker_cache(self, mock_logger):
        """Test that Service Worker cache is removed during pruning."""
        # Create Service Worker cache structure
        sw_cache_dir = os.path.join("Service Worker", "CacheStorage")
        self._create_test_dir(sw_cache_dir)
        self._create_test_file(os.path.join(sw_cache_dir, "cache_data.bin"), 4096)
        
        # Verify directory exists before pruning
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, sw_cache_dir)))
        
        # Run pruning
        _prune_chrome_profile(self.test_profile_dir)
        
        # Verify Service Worker cache is removed
        self.assertFalse(os.path.exists(os.path.join(self.test_profile_dir, sw_cache_dir)))

    @patch('api.tasks.browser_agent_tasks.logger')
    def test_profile_reset_when_oversized(self, mock_logger):
        """Test that profile is reset when it exceeds size limit after pruning."""
        # Create a large file that won't be pruned (exceeding 500MB limit)
        large_file_size = CHROME_PROFILE_MAX_SIZE_BYTES + (10 * 1024 * 1024)  # 510MB
        self._create_test_file("LargeImportantData.bin", large_file_size)
        
        # Verify file exists and directory is oversized
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "LargeImportantData.bin")))
        initial_size = self._get_dir_size(self.test_profile_dir)
        self.assertGreater(initial_size, CHROME_PROFILE_MAX_SIZE_BYTES)
        
        # Run pruning
        _prune_chrome_profile(self.test_profile_dir)
        
        # Verify directory was reset (should be empty now)
        final_size = self._get_dir_size(self.test_profile_dir)
        self.assertEqual(final_size, 0)
        
        # Verify directory still exists but is empty
        self.assertTrue(os.path.exists(self.test_profile_dir))
        self.assertEqual(len(os.listdir(self.test_profile_dir)), 0)
        
        # Verify appropriate logging occurred
        mock_logger.info.assert_called()
        log_calls = [call[0][0] for call in mock_logger.info.call_args_list]
        reset_logged = any("Resetting directory" in msg for msg in log_calls)
        self.assertTrue(reset_logged, "Expected reset message in logs")

    @tag("batch_browser_profile")
    @patch('api.tasks.browser_agent_tasks.logger')
    def test_profile_not_reset_when_within_limit(self, mock_logger):
        """Test that profile is NOT reset when within size limit after pruning."""
        # Create some cache files that will be pruned
        self._create_test_dir("Cache")
        self._create_test_file("Cache/large_cache.bin", 10 * 1024 * 1024)  # 10MB cache
        
        # Create a small important file that won't be pruned
        self._create_test_file("Preferences", 1024)  # 1KB
        
        # Run pruning
        _prune_chrome_profile(self.test_profile_dir)
        
        # Verify important file still exists (profile not reset)
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "Preferences")))
        
        # Verify cache was removed
        self.assertFalse(os.path.exists(os.path.join(self.test_profile_dir, "Cache")))
        
        # Verify final size is small
        final_size = self._get_dir_size(self.test_profile_dir)
        self.assertLess(final_size, CHROME_PROFILE_MAX_SIZE_BYTES)

    @tag("batch_browser_profile")
    @patch('api.tasks.browser_agent_tasks.logger')
    def test_size_logging(self, mock_logger):
        """Test that before/after size logging works correctly."""
        # Create some files
        self._create_test_file("Cache/data.bin", 5 * 1024 * 1024)  # 5MB cache (will be pruned)
        self._create_test_file("Preferences", 1024)  # 1KB (will remain)
        
        # Run pruning
        _prune_chrome_profile(self.test_profile_dir)
        
        # Verify size logging occurred
        log_calls = [call[0][0] for call in mock_logger.info.call_args_list]
        
        # Check for before/after size logging
        before_logged = any("size before pruning" in msg for msg in log_calls)
        after_logged = any("size after pruning" in msg for msg in log_calls)
        
        self.assertTrue(before_logged, "Expected 'before pruning' size log")
        self.assertTrue(after_logged, "Expected 'after pruning' size log")

    @patch('api.tasks.browser_agent_tasks.logger')
    def test_crashpad_directories_removed(self, mock_logger):
        """Test that Crashpad crash dump directories are removed."""
        # Create Crashpad directories
        self._create_test_dir(os.path.join("Crashpad", "completed"))
        self._create_test_file(os.path.join("Crashpad", "completed", "crash1.dmp"), 2048)
        self._create_test_dir(os.path.join("Crashpad", "pending"))
        self._create_test_file(os.path.join("Crashpad", "pending", "crash2.dmp"), 1024)
        
        # Verify directories exist before pruning
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "Crashpad", "completed")))
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "Crashpad", "pending")))
        
        # Run pruning
        _prune_chrome_profile(self.test_profile_dir)
        
        # Verify Crashpad directories are removed
        self.assertFalse(os.path.exists(os.path.join(self.test_profile_dir, "Crashpad", "completed")))
        self.assertFalse(os.path.exists(os.path.join(self.test_profile_dir, "Crashpad", "pending")))

    @patch('api.tasks.browser_agent_tasks.logger')
    def test_safe_browsing_removed(self, mock_logger):
        """Test that Safe Browsing directory is removed."""
        # Create Safe Browsing directory
        self._create_test_dir("Safe Browsing")
        self._create_test_file("Safe Browsing/database.bin", 3072)
        
        # Verify directory exists before pruning
        self.assertTrue(os.path.exists(os.path.join(self.test_profile_dir, "Safe Browsing")))
        
        # Run pruning
        _prune_chrome_profile(self.test_profile_dir)
        
        # Verify Safe Browsing directory is removed
        self.assertFalse(os.path.exists(os.path.join(self.test_profile_dir, "Safe Browsing"))) 
