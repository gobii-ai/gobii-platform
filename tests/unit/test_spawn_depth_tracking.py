"""Tests for spawn_web_task child-count tracking.

Validates that parallel spawned tasks do not affect recursion depth
argument but correctly increment the outstanding-children counter in
Redis so the agent loop keeps the cycle open while children run.
"""

import threading
import time
from unittest.mock import patch, MagicMock, call
from django.test import TransactionTestCase, tag
from django.contrib.auth import get_user_model

from api.models import PersistentAgent, BrowserUseAgent, BrowserUseAgentTask
from api.agent.core.budget import AgentBudgetManager, BudgetContext, set_current_context as set_budget_context
from api.agent.tools.spawn_web_task import execute_spawn_web_task
from tests.utils.redis_test_mixin import RedisIsolationMixin


@tag("batch_spawn_depth")
class SpawnDepthTrackingTests(RedisIsolationMixin, TransactionTestCase):
    """Test that parallel spawn_web_task calls correctly track depth."""
    
    def setUp(self):
        """Set up test data."""
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_user(
            username='test@example.com',
            email='test@example.com'
        )
        
        # Create a browser agent
        with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
            self.browser_agent = BrowserUseAgent.objects.create(
                user=self.user,
                name="Test Browser Agent"
            )
        
        # Create a persistent agent
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Test Agent",
            charter="Test",
            browser_use_agent=self.browser_agent
        )
        
        # Initialize a budget cycle
        self.budget_id, _, _ = AgentBudgetManager.find_or_start_cycle(
            agent_id=str(self.agent.id),
            max_steps=100,
            max_depth=3  # Allow depth up to 3 for testing
        )
        
        # Create initial branch at depth 0
        self.branch_id = AgentBudgetManager.create_branch(
            agent_id=str(self.agent.id),
            budget_id=self.budget_id,
            depth=0
        )
        
        # Set up budget context
        self.budget_ctx = BudgetContext(
            agent_id=str(self.agent.id),
            budget_id=self.budget_id,
            branch_id=self.branch_id,
            depth=0,
            max_steps=100,
            max_depth=3
        )
    
    @patch('api.tasks.browser_agent_tasks.process_browser_use_task')
    def test_parallel_spawn_increments_outstanding_children(self, mock_process_task):
        """Parallel spawns should each pass recursion depth=1 and increment outstanding-children counter."""
        mock_process_task.delay = MagicMock()
        
        # Results will be stored here by each thread
        results = []
        spawn_errors = []
        observed_depths = []
        
        def spawn_task(task_num):
            """Function to be run in each thread."""
            try:
                # Set budget context for this thread
                set_budget_context(self.budget_ctx)
                
                # Small delay to ensure threads run concurrently
                time.sleep(0.01 * task_num)
                
                result = execute_spawn_web_task(
                    self.agent,
                    {"prompt": f"Task {task_num}"}
                )
                results.append(result)
                
                # Check if spawn was successful
                if result.get("status") == "error":
                    spawn_errors.append((task_num, result.get("message")))
                
                # Extract the depth that was passed to the spawned task
                if mock_process_task.delay.called:
                    calls = mock_process_task.delay.call_args_list
                    if calls:
                        # Get the most recent call's depth argument
                        last_call = calls[-1]
                        depth = last_call[1].get('depth') if last_call[1] else None
                        if depth is not None:
                            observed_depths.append((task_num, depth))
                
            except Exception as e:
                spawn_errors.append((task_num, str(e)))
        
        # Spawn 3 tasks in parallel from depth 0
        threads = []
        for i in range(3):
            thread = threading.Thread(target=spawn_task, args=(i,))
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join(timeout=5)
        
        # Check results
        self.assertEqual(len(results), 3, "All three spawns should complete")
        
        # Check for any errors (especially "recursion limit reached")
        if spawn_errors:
            self.fail(f"Some spawns failed: {spawn_errors}")
        
        # All spawned tasks should be at depth 1 since they're all spawned from depth 0 in parallel
        expected_depth = 1
        
        # Sort observed depths by task number for consistent reporting
        observed_depths.sort(key=lambda x: x[0])
        
        # Check that all tasks are at the same depth
        for task_num, depth in observed_depths:
            self.assertEqual(
                depth, expected_depth,
                f"Task {task_num} should be at depth {expected_depth}, but is at depth {depth}. "
                f"This indicates parallel spawns are incorrectly sharing/mutating the depth counter. "
                f"All observed depths: {observed_depths}"
            )
        
        # Verify the branch counter in Redis reflects all outstanding children (3)
        final_branch_depth = AgentBudgetManager.get_branch_depth(
            agent_id=str(self.agent.id),
            branch_id=self.branch_id
        )
        self.assertEqual(
            final_branch_depth, 3,
            f"Branch counter should equal number of parallel spawns; got {final_branch_depth}"
        )
    
    @patch('api.tasks.browser_agent_tasks.process_browser_use_task')
    def test_sequential_spawn_depth_tracking(self, mock_process_task):
        """Test that sequential spawns work correctly (baseline test).
        
        This should pass even with the current buggy implementation
        since there's no parallelism.
        """
        mock_process_task.delay = MagicMock()
        
        # Set budget context
        set_budget_context(self.budget_ctx)
        
        # First spawn (from depth 0)
        result1 = execute_spawn_web_task(
            self.agent,
            {"prompt": "Task 1"}
        )
        self.assertEqual(result1.get("status"), "pending")
        
        # Get the depth of the first spawned task
        call1 = mock_process_task.delay.call_args_list[0]
        depth1 = call1[1].get('depth')
        self.assertEqual(depth1, 1, "First spawn should be at depth 1")
        
        # Reset branch depth back to 0 for second spawn
        AgentBudgetManager.set_branch_depth(
            agent_id=str(self.agent.id),
            branch_id=self.branch_id,
            depth=0
        )
        
        # Second spawn (also from depth 0)
        result2 = execute_spawn_web_task(
            self.agent,
            {"prompt": "Task 2"}
        )
        self.assertEqual(result2.get("status"), "pending")
        
        # Get the depth of the second spawned task
        call2 = mock_process_task.delay.call_args_list[1]
        depth2 = call2[1].get('depth')
        self.assertEqual(depth2, 1, "Second spawn should also be at depth 1")
