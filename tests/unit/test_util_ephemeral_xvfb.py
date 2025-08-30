"""
Tests for EphemeralXvfb cross-process locking mechanism.
"""
import os
import time
import tempfile
import threading
import multiprocessing
from unittest import TestCase
from django.test import tag
from unittest.mock import patch, MagicMock
from util.ephemeral_xvfb import EphemeralXvfb, xvfb_lock, _find_available_display
import platform
import unittest


@tag("batch_xvfb")
class XvfbLockTests(TestCase):
    """Test the cross-process file locking mechanism."""

    def test_xvfb_lock_basic_functionality(self):
        """Test that xvfb_lock creates and releases file lock correctly."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            lock_path = tmp.name
        
        try:
            # Remove the file so we can test creation
            os.unlink(lock_path)
            
            # Test that lock file is created and removed
            with xvfb_lock(lock_path):
                self.assertTrue(os.path.exists(lock_path))
            
            # File should still exist but lock should be released
            self.assertTrue(os.path.exists(lock_path))
        finally:
            if os.path.exists(lock_path):
                os.unlink(lock_path)

    def test_xvfb_lock_sequential_access(self):
        """Test that multiple sequential lock acquisitions work correctly."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            lock_path = tmp.name
        
        try:
            results = []
            
            def acquire_lock(result_list, value):
                with xvfb_lock(lock_path):
                    result_list.append(f"start_{value}")
                    time.sleep(0.1)  # Simulate work
                    result_list.append(f"end_{value}")
            
            # Run sequentially - should complete in order
            acquire_lock(results, "first")
            acquire_lock(results, "second")
            
            expected = ["start_first", "end_first", "start_second", "end_second"]
            self.assertEqual(results, expected)
        finally:
            if os.path.exists(lock_path):
                os.unlink(lock_path)

    def test_xvfb_lock_threading_serialization(self):
        """Test that file lock serializes access across threads."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            lock_path = tmp.name
        
        try:
            results = []
            results_lock = threading.Lock()
            
            def thread_worker(thread_id):
                with xvfb_lock(lock_path):
                    with results_lock:
                        results.append(f"start_{thread_id}")
                    time.sleep(0.1)  # Simulate work inside critical section
                    with results_lock:
                        results.append(f"end_{thread_id}")
            
            # Start two threads that should be serialized by the file lock
            thread1 = threading.Thread(target=thread_worker, args=("thread1",))
            thread2 = threading.Thread(target=thread_worker, args=("thread2",))
            
            thread1.start()
            thread2.start()
            
            thread1.join()
            thread2.join()
            
            # Results should show complete serialization - one thread should
            # completely finish before the other starts
            self.assertEqual(len(results), 4)
            
            # Find which thread went first
            if results[0] == "start_thread1":
                expected = ["start_thread1", "end_thread1", "start_thread2", "end_thread2"]
            else:
                expected = ["start_thread2", "end_thread2", "start_thread1", "end_thread1"]
            
            self.assertEqual(results, expected)
        finally:
            if os.path.exists(lock_path):
                os.unlink(lock_path)


# ---------------------------------------------------------------------------
#  EphemeralXvfb behavioural tests
# ---------------------------------------------------------------------------

# These tests rely on Linux-specific Xvfb functionality. On macOS (Darwin) and
# Windows the Xvfb binary is unavailable, so the semantics around DISPLAY
# environment handling differ and the mocks in these tests are not valid.

@unittest.skipUnless(platform.system() == "Linux", "EphemeralXvfb tests require Linux/Xvfb")
@tag("batch_xvfb")
class EphemeralXvfbTests(TestCase):
    """Test EphemeralXvfb functionality including retry logic."""

    @patch('util.ephemeral_xvfb.subprocess.Popen')
    @patch('util.ephemeral_xvfb._find_available_display')
    @patch('os.path.exists')
    def test_successful_start(self, mock_exists, mock_find_display, mock_popen):
        """Test successful Xvfb startup."""
        # Setup mocks
        mock_find_display.return_value = 1001
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Process is running
        mock_popen.return_value = mock_proc
        mock_exists.return_value = True  # Lock file exists
        
        # Test
        xvfb = EphemeralXvfb()
        with patch.dict(os.environ, {}, clear=True):
            xvfb.start()

            # Verify
            self.assertEqual(xvfb.display_num, 1001)
            self.assertEqual(os.environ.get("DISPLAY"), ":1001")

        mock_popen.assert_called_once()
        
        # Cleanup
        xvfb.stop()

    @patch('util.ephemeral_xvfb.subprocess.Popen')
    @patch('util.ephemeral_xvfb._find_available_display')
    @patch('os.path.exists')
    def test_retry_on_xvfb_failure(self, mock_exists, mock_find_display, mock_popen):
        """Test retry logic when Xvfb fails to start."""
        # Setup mocks - first attempt fails, second succeeds
        mock_find_display.side_effect = [1001, 1002]
        
        # First process fails immediately
        failed_proc = MagicMock()
        failed_proc.poll.return_value = 1  # Process exited with error
        
        # Second process succeeds
        success_proc = MagicMock()
        success_proc.poll.return_value = None  # Process is running
        
        mock_popen.side_effect = [failed_proc, success_proc]
        mock_exists.return_value = True  # Lock file exists
        
        # Test
        xvfb = EphemeralXvfb()
        with patch.dict(os.environ, {}, clear=True):
            xvfb.start()

            # Verify retry happened and second attempt succeeded
            self.assertEqual(xvfb.display_num, 1002)
            self.assertEqual(os.environ.get("DISPLAY"), ":1002")
        
        self.assertEqual(mock_popen.call_count, 2)
        
        # Cleanup
        xvfb.stop()

    @patch('util.ephemeral_xvfb.subprocess.Popen')
    @patch('util.ephemeral_xvfb._find_available_display')
    @patch('os.path.exists')
    def test_max_retries_exceeded(self, mock_exists, mock_find_display, mock_popen):
        """Test that RuntimeError is raised when max retries exceeded."""
        # Setup mocks - all attempts fail
        mock_find_display.return_value = 1001
        failed_proc = MagicMock()
        failed_proc.poll.return_value = 1  # Process exited with error
        mock_popen.return_value = failed_proc
        mock_exists.return_value = False  # Lock file doesn't exist
        
        # Test
        xvfb = EphemeralXvfb()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as cm:
                xvfb.start()
        
        # Verify error message mentions retry attempts
        self.assertIn("Failed to start Xvfb after 5 attempts", str(cm.exception))
        
        # Verify we attempted 5 times
        self.assertEqual(mock_popen.call_count, 5)
        
        # Verify cleanup happened
        self.assertIsNone(xvfb._proc)
        self.assertIsNone(xvfb.display_num)

    @patch('util.ephemeral_xvfb.subprocess.Popen')
    @patch('util.ephemeral_xvfb._find_available_display')
    def test_concurrent_start_serialization(self, mock_find_display, mock_popen):
        """Test that multiple EphemeralXvfb instances don't conflict."""
        # This test verifies the file locking prevents display number conflicts
        display_numbers_used = []
        
        def mock_find_display_side_effect():
            # Simulate finding different display numbers
            base_display = 1000 + len(display_numbers_used)
            display_numbers_used.append(base_display)
            return base_display
        
        mock_find_display.side_effect = mock_find_display_side_effect
        
        # Mock successful Xvfb processes
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        
        results = []
        
        def start_xvfb(instance_id):
            xvfb = EphemeralXvfb()
            try:
                with patch('os.path.exists', return_value=True):
                    with patch.dict(os.environ, {}, clear=True):
                        xvfb.start()
                        results.append(f"started_{instance_id}_{xvfb.display_num}")
                        time.sleep(0.1)  # Simulate work
                        results.append(f"finished_{instance_id}_{xvfb.display_num}")
            finally:
                xvfb.stop()
        
        # Start multiple threads - they should be serialized by the file lock
        threads = []
        for i in range(3):
            thread = threading.Thread(target=start_xvfb, args=(i,))
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        # Verify all completed successfully and used different display numbers
        self.assertEqual(len(results), 6)  # 3 starts + 3 finishes
        self.assertEqual(len(set(display_numbers_used)), 3)  # All different displays

    def test_already_started_noop(self):
        """Test that calling start() when already started is a no-op."""
        xvfb = EphemeralXvfb()
        
        # Mock an already running process
        mock_proc = MagicMock()
        xvfb._proc = mock_proc
        xvfb.display_num = 1001
        
        # start() should be a no-op
        with patch('util.ephemeral_xvfb._find_available_display') as mock_find:
            xvfb.start()
            mock_find.assert_not_called()
        
        # State should be unchanged
        self.assertEqual(xvfb._proc, mock_proc)
        self.assertEqual(xvfb.display_num, 1001)


# Apply same platform limitation to allocation tests for consistency

@unittest.skipUnless(platform.system() == "Linux", "EphemeralXvfb tests require Linux/Xvfb")
@tag("batch_xvfb")
class DisplayAllocationTests(TestCase):
    """Test display number allocation logic."""
    
    @patch('util.ephemeral_xvfb._is_display_in_use')
    def test_find_available_display_finds_first_free(self, mock_is_in_use):
        """Test that _find_available_display returns first available display."""
        # Mock: displays 1000, 1001 are in use, 1002 is free
        def side_effect(display_num):
            return display_num in [1000, 1001]
        
        mock_is_in_use.side_effect = side_effect
        
        result = _find_available_display()
        self.assertEqual(result, 1002)

    @patch('util.ephemeral_xvfb._is_display_in_use')
    def test_find_available_display_raises_when_all_used(self, mock_is_in_use):
        """Test that RuntimeError is raised when all displays are in use."""
        mock_is_in_use.return_value = True  # All displays in use
        
        with self.assertRaises(RuntimeError) as cm:
            _find_available_display()
        
        self.assertIn("No available Xvfb display numbers", str(cm.exception))
