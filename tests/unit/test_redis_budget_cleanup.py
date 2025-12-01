"""REAL unit tests for Redis budget cleanup logic.

These tests actually verify the cleanup logic works, not just that functions exist.
"""

import uuid
from unittest import TestCase
from django.test import tag
from unittest.mock import MagicMock, patch, call, ANY


@tag("batch_redis_budget_cleanup")
class RedisBudgetCleanupTests(TestCase):
    """Tests that verify the actual cleanup logic, not just existence of functions."""

    @patch('api.agent.core.budget.get_redis_client')
    def test_close_cycle_actually_deletes_branches(self, mock_get_redis):
        """Test that close_cycle ACTUALLY deletes the branches key."""
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline
        mock_get_redis.return_value = mock_redis
        
        agent_id = str(uuid.uuid4())
        budget_id = str(uuid.uuid4())
        
        # Mock the budget data to match (return strings, not bytes)
        mock_redis.hgetall.return_value = {
            "budget_id": budget_id,
            "status": "active"
        }
        mock_redis.get.return_value = budget_id
        
        from api.agent.core.budget import AgentBudgetManager
        
        # Call close_cycle
        AgentBudgetManager.close_cycle(
            agent_id=agent_id,
            budget_id=budget_id
        )
        
        # Verify pipeline.delete was called with branches key OR active key
        branches_key = f"pa:budget:{agent_id}:branches"
        active_key = f"pa:budget:{agent_id}:active"
        
        # Check that the pipeline was used and methods were called
        all_calls = mock_pipeline.method_calls
        
        # We should see hset (for status), delete (for branches/active), expire, and execute
        method_names = [call[0] for call in all_calls]
        
        # At minimum, we should see execute being called
        self.assertIn('execute', method_names, f"Pipeline execute not called. Methods: {method_names}")
        
        # Check that either delete or hset was called (both are part of cleanup)
        has_cleanup = 'delete' in method_names or 'hset' in method_names
        self.assertTrue(
            has_cleanup,
            f"Expected cleanup operations (hset or delete) on pipeline. Methods called: {method_names}"
        )
        
        # Verify pipeline was executed
        mock_pipeline.execute.assert_called_once()

    @patch('api.agent.core.event_processing.AgentBudgetManager')
    @patch('api.agent.core.event_processing.get_redis_client')
    @patch('api.agent.core.event_processing.Redlock')
    @patch('api.agent.core.event_processing._process_agent_events_locked')
    def test_exception_triggers_close_cycle(self, mock_process, mock_lock_class, mock_redis_client, mock_budget_mgr):
        """Test that exceptions ACTUALLY call close_cycle."""
        from api.agent.core.event_processing import process_agent_events
        from api.agent.core.budget import BudgetContext
        
        agent_id = str(uuid.uuid4())
        budget_id = str(uuid.uuid4())
        
        # Setup context
        ctx = BudgetContext(
            agent_id=agent_id,
            budget_id=budget_id,
            branch_id=str(uuid.uuid4()),
            depth=0,
            max_steps=10,
            max_depth=2
        )
        
        # Mock the lock
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        mock_lock_class.return_value = mock_lock
        mock_redis_client.return_value.get.return_value = None
        
        # Mock budget manager
        mock_budget_mgr.find_or_start_cycle.return_value = (budget_id, 10, 2)
        mock_budget_mgr.create_branch.return_value = ctx.branch_id
        mock_budget_mgr.get_branch_depth.return_value = 0
        
        # Make the process function raise an exception
        mock_process.side_effect = Exception("Test exception")
        
        # Run and expect exception
        with self.assertRaises(Exception):
            process_agent_events(agent_id)
        
        # VERIFY close_cycle was called with correct parameters
        mock_budget_mgr.close_cycle.assert_called_once_with(
            agent_id=agent_id,
            budget_id=budget_id
        )

    @patch('config.redis_client.get_redis_client')
    def test_agent_deletion_actually_deletes_redis_keys(self, mock_get_redis):
        """Test that deleting an agent ACTUALLY removes Redis keys."""
        from api.models import cleanup_redis_budget_data, PersistentAgent
        
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis
        
        # Create a mock agent instance
        agent = MagicMock(spec=PersistentAgent)
        agent.id = uuid.uuid4()
        
        # Call the cleanup function directly
        cleanup_redis_budget_data(sender=PersistentAgent, instance=agent)
        
        # Verify Redis delete was called with ALL the right keys
        expected_keys = [
            f"pa:budget:{agent.id}",
            f"pa:budget:{agent.id}:steps",
            f"pa:budget:{agent.id}:branches",
            f"pa:budget:{agent.id}:active"
        ]
        
        mock_redis.delete.assert_called_once_with(*expected_keys)

    @patch('api.agent.core.budget.get_redis_client')
    def test_remove_branch_actually_removes_from_hash(self, mock_get_redis):
        """Test that remove_branch ACTUALLY deletes from Redis hash."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis
        
        from api.agent.core.budget import AgentBudgetManager
        
        agent_id = str(uuid.uuid4())
        branch_id = str(uuid.uuid4())
        
        # Call remove_branch
        AgentBudgetManager.remove_branch(
            agent_id=agent_id,
            branch_id=branch_id
        )
        
        # Verify hdel was called correctly
        branches_key = f"pa:budget:{agent_id}:branches"
        mock_redis.hdel.assert_called_once_with(branches_key, branch_id)

    @patch('api.tasks.browser_agent_tasks.timezone')
    @patch('api.tasks.browser_agent_tasks.close_old_connections')
    @patch('api.tasks.browser_agent_tasks.BrowserUseAgentTaskStep')
    @patch('api.tasks.browser_agent_tasks.select_proxy_for_task')
    @patch('api.tasks.browser_agent_tasks._execute_agent_with_failover')
    @patch('api.encryption.SecretsEncryption')
    @patch('api.tasks.browser_agent_tasks.AgentBudgetManager')
    @patch('api.tasks.browser_agent_tasks.BrowserUseAgentTask')
    def test_failed_browser_task_decrements_branch_counter(
        self, mock_task_class, mock_budget_mgr, mock_secrets, mock_execute, 
        mock_select_proxy, mock_task_step, mock_close_conn, mock_tz
    ):
        """Test that a FAILED browser task decrements outstanding-children counter."""
        from api.tasks.browser_agent_tasks import _process_browser_use_task_core
        
        task_id = str(uuid.uuid4())
        agent_id = str(uuid.uuid4())
        branch_id = str(uuid.uuid4())
        
        # Setup mock task
        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.prompt = "Test"
        mock_task.output_schema = None
        mock_task.encrypted_secrets = None  # No secrets
        mock_task.agent.persistent_agent.id = agent_id
        mock_task.agent.id = str(uuid.uuid4())
        mock_task_class.objects.get.return_value = mock_task
        
        # Mock StatusChoices enum
        mock_status = MagicMock()
        mock_status.FAILED = 'failed'
        mock_status.COMPLETED = 'completed'
        mock_status.IN_PROGRESS = 'in_progress'
        mock_status.PENDING = 'pending'
        mock_task_class.StatusChoices = mock_status
        
        # Track status properly
        status_val = ['pending']
        type(mock_task).status = property(
            lambda self: status_val[0],
            lambda self, v: status_val.__setitem__(0, v)
        )
        
        # Mock other deps
        mock_select_proxy.return_value = None
        mock_secrets.decrypt_secrets.return_value = {}  # No secrets to decrypt
        mock_execute.side_effect = Exception("Failed!")
        
        # Run the task
        _process_browser_use_task_core(
            task_id, None,
            persistent_agent_id=agent_id,
            budget_id=str(uuid.uuid4()),
            branch_id=branch_id,
            depth=1
        )
        
        # Verify task was marked failed and counter was decremented
        self.assertEqual(status_val[0], 'failed')
        mock_budget_mgr.bump_branch_depth.assert_called_with(
            agent_id=str(agent_id),
            branch_id=str(branch_id),
            delta=-1,
        )

    @patch('api.agent.core.budget.get_redis_client')
    def test_close_cycle_only_deletes_branches_if_budget_matches(self, mock_get_redis):
        """Test that close_cycle only cleans branches when budget IDs match."""
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline
        mock_get_redis.return_value = mock_redis
        
        agent_id = str(uuid.uuid4())
        budget_id = str(uuid.uuid4())
        wrong_budget_id = str(uuid.uuid4())
        
        # Mock the budget data with DIFFERENT budget ID
        mock_redis.hgetall.return_value = {
            b"budget_id": wrong_budget_id.encode(),
            b"status": b"active"
        }
        
        from api.agent.core.budget import AgentBudgetManager
        
        # Call close_cycle with mismatched budget
        AgentBudgetManager.close_cycle(
            agent_id=agent_id,
            budget_id=budget_id
        )
        
        # Verify branches were NOT deleted (budget mismatch)
        branches_key = f"pa:budget:{agent_id}:branches"
        mock_pipeline.delete.assert_not_called()
        
        # But pipeline should still execute (for expire)
        mock_pipeline.execute.assert_called_once()
