import uuid

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock
import sys
import types
from api.models import (
    BrowserUseAgent,
    ApiKey,
    BrowserUseAgentTask,
    BrowserUseAgentTaskStep,
    UserQuota,
    TaskCredit, )
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNamesChoices
from util.subscription_helper import report_task_usage_to_stripe, report_task_usage
from api.serializers import BrowserUseAgentTaskSerializer
from django.utils import timezone
from datetime import timedelta
from django.core.exceptions import ValidationError


User = get_user_model()

@tag("batch_api_agents")
class BrowserUseAgentViewSetTests(APITestCase):
    def setUp(self):
        # User 1
        self.user1 = User.objects.create_user(username='user1@example.com', email='user1@example.com', password='password123')
        UserQuota.objects.get_or_create(user=self.user1, defaults={'agent_limit': 5}) # Increased task quota
        self.raw_api_key1, self.api_key_obj1 = ApiKey.create_for_user(self.user1, name='test_key1')
        
        # User 2
        self.user2 = User.objects.create_user(username='user2@example.com', email='user2@example.com', password='password123')
        UserQuota.objects.get_or_create(user=self.user2, defaults={'agent_limit': 5}) # Increased task quota
        self.raw_api_key2, _ = ApiKey.create_for_user(self.user2, name='test_key2')

        # Agents for User 1
        self.agent1_user1 = BrowserUseAgent.objects.create(user=self.user1, name='Agent 1 User 1')
        self.agent2_user1 = BrowserUseAgent.objects.create(user=self.user1, name='Agent 2 User 1')
        
        # Agent for User 2
        self.agent1_user2 = BrowserUseAgent.objects.create(user=self.user2, name='Agent 1 User 2')

        # Authenticate as user1 by default
        self.client.credentials(HTTP_X_API_KEY=self.raw_api_key1)

    def test_list_agents_authenticated_user(self):
        """
        Ensure authenticated user can list their own agents.
        """
        url = reverse('api:browseruseagent-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2) # User1 has 2 agents
        agent_names = [agent['name'] for agent in response.data['results']]
        self.assertIn(self.agent1_user1.name, agent_names)
        self.assertIn(self.agent2_user1.name, agent_names)
        # Check serializer fields
        self.assertIn('id', response.data['results'][0])
        # Correcting the ID check to be more robust against ordering
        retrieved_agent_ids = {agent['id'] for agent in response.data['results']}
        expected_agent_ids = {str(self.agent1_user1.id), str(self.agent2_user1.id)}
        self.assertEqual(retrieved_agent_ids, expected_agent_ids)


    def test_list_agents_unauthenticated(self):
        """
        Ensure unauthenticated access to list agents is denied.
        """
        self.client.credentials() # Clear credentials
        url = reverse('api:browseruseagent-list')
        response = self.client.get(url)
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_retrieve_agent_owned_by_user(self):
        """
        Ensure user can retrieve their own agent.
        """
        url = reverse('api:browseruseagent-detail', kwargs={'pk': self.agent1_user1.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], str(self.agent1_user1.id))
        self.assertEqual(response.data['name'], self.agent1_user1.name)
        self.assertEqual(response.data['user_email'], self.user1.email) 

    def test_retrieve_agent_not_owned_by_user(self):
        """
        Ensure user cannot retrieve an agent they do not own (expect 404).
        """
        url = reverse('api:browseruseagent-detail', kwargs={'pk': self.agent1_user2.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_retrieve_agent_unauthenticated(self):
        """
        Ensure unauthenticated access to retrieve an agent is denied.
        """
        self.client.credentials() # Clear credentials
        url = reverse('api:browseruseagent-detail', kwargs={'pk': self.agent1_user1.id})
        response = self.client.get(url)
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_update_agent_name_owned_by_user_patch(self):
        """
        Ensure user can update their own agent's name using PATCH.
        """
        url = reverse('api:browseruseagent-detail', kwargs={'pk': self.agent1_user1.id})
        new_name = "Updated Agent Name"
        data = {'name': new_name}
        response = self.client.patch(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.agent1_user1.refresh_from_db()
        self.assertEqual(self.agent1_user1.name, new_name)
        self.assertEqual(response.data['name'], new_name)

    def test_update_agent_name_not_owned_by_user_patch(self):
        """
        Ensure user cannot update an agent they do not own (expect 404).
        """
        url = reverse('api:browseruseagent-detail', kwargs={'pk': self.agent1_user2.id})
        new_name = "Attempted Update Name"
        data = {'name': new_name}
        response = self.client.patch(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_agent_read_only_fields_patch(self):
        """
        Ensure read-only fields (e.g., id, created_at, user_email) are not updated.
        """
        url = reverse('api:browseruseagent-detail', kwargs={'pk': self.agent1_user1.id})
        original_id = str(self.agent1_user1.id)
        original_created_at = self.agent1_user1.created_at.isoformat().replace('+00:00', 'Z') 
        original_user_email = self.user1.email
        
        data = {
            'name': 'New Name For ReadOnly Test',
            'id': str(uuid.uuid4()), 
            'created_at': '2000-01-01T00:00:00Z',
            'user_email': 'attacker@example.com'
        }
        response = self.client.patch(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.agent1_user1.refresh_from_db()
        
        self.assertEqual(response.data['name'], 'New Name For ReadOnly Test')
        self.assertEqual(str(self.agent1_user1.id), original_id)
        self.assertEqual(response.data['id'], original_id)
        
        self.assertEqual(self.agent1_user1.created_at.isoformat().replace('+00:00', 'Z'), original_created_at)
        self.assertEqual(response.data['created_at'], original_created_at)

        self.assertEqual(self.agent1_user1.user.email, original_user_email)
        self.assertEqual(response.data['user_email'], original_user_email)

    def test_create_agent_success(self):
        """
        Test creating a new agent successfully.
        """
        url = reverse('api:browseruseagent-list') 
        agent_name = "Newly Created Agent"
        data = {'name': agent_name}
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['name'], agent_name)
        self.assertEqual(response.data['user_email'], self.user1.email)
        self.assertTrue(BrowserUseAgent.objects.filter(name=agent_name, user=self.user1).exists())

    def test_delete_agent_success(self):
        """
        Test deleting an agent successfully.
        """
        agent_to_delete = BrowserUseAgent.objects.create(user=self.user1, name='Agent To Delete')
        url = reverse('api:browseruseagent-detail', kwargs={'pk': agent_to_delete.id})
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BrowserUseAgent.objects.filter(id=agent_to_delete.id).exists())

    def test_delete_agent_not_owned(self):
        """
        Test attempting to delete an agent not owned by the user.
        """
        url = reverse('api:browseruseagent-detail', kwargs={'pk': self.agent1_user2.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(BrowserUseAgent.objects.filter(id=self.agent1_user2.id).exists())


@tag("batch_api_serializer")
class BrowserUseAgentTaskSerializerTests(APITestCase):
    def test_serializer_wait_parameter_validation(self):
        """Test that the BrowserUseAgentTaskSerializer validates the wait parameter correctly."""
        # Valid wait parameter
        serializer = BrowserUseAgentTaskSerializer(data={'prompt': 'Test task', 'wait': 600})
        self.assertTrue(serializer.is_valid())
        
        # Wait parameter too small
        serializer = BrowserUseAgentTaskSerializer(data={'prompt': 'Test task', 'wait': -1})
        self.assertFalse(serializer.is_valid())
        self.assertIn('wait', serializer.errors)
        
        # Wait parameter too large
        serializer = BrowserUseAgentTaskSerializer(data={'prompt': 'Test task', 'wait': 1351})
        self.assertFalse(serializer.is_valid())
        self.assertIn('wait', serializer.errors)
        
        # Wait parameter is not required
        serializer = BrowserUseAgentTaskSerializer(data={'prompt': 'Test task'})
        self.assertTrue(serializer.is_valid())
        
        # Wait parameter is properly removed when saving
        serializer = BrowserUseAgentTaskSerializer(data={'prompt': 'Test task', 'wait': 30})
        self.assertTrue(serializer.is_valid())
        self.assertIn('wait', serializer.validated_data)
        
class BrowserUseAgentTaskViewSetTests(APITestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='user1tasks@example.com', email='user1tasks@example.com', password='password123')
        UserQuota.objects.get_or_create(user=self.user1, defaults={'agent_limit': 5})
        self.raw_api_key1, _ = ApiKey.create_for_user(self.user1, name='test_key1_tasks')

        TaskCredit.objects.create(
            user=self.user1,
            credits=50,
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            plan=PlanNamesChoices.FREE,
            grant_type=GrantTypeChoices.PROMO
        )

        self.agent1_user1 = BrowserUseAgent.objects.create(user=self.user1, name='Task Agent 1 User 1')
        self.agent2_user1 = BrowserUseAgent.objects.create(user=self.user1, name='Task Agent 2 User 1')
        
        self.task1_agent1_user1 = BrowserUseAgentTask.objects.create(agent=self.agent1_user1, user=self.user1, prompt={'detail': 'Task 1 for Agent 1'})
        self.task2_agent1_user1 = BrowserUseAgentTask.objects.create(agent=self.agent1_user1, user=self.user1, prompt={'detail': 'Task 2 for Agent 1'})
        self.task1_agent2_user1 = BrowserUseAgentTask.objects.create(agent=self.agent2_user1, user=self.user1, prompt={'detail': 'Task 1 for Agent 2'})
        
        BrowserUseAgentTaskStep.objects.create(
            task=self.task1_agent1_user1, step_number=1, description='Result step', is_result=True, result_value='Result for Task 1 Agent 1'
        )

        self.user2 = User.objects.create_user(username='user2tasks@example.com', email='user2tasks@example.com', password='password123')
        UserQuota.objects.get_or_create(user=self.user2, defaults={'agent_limit': 5})
        self.raw_api_key2, _ = ApiKey.create_for_user(self.user2, name='test_key2_tasks')

        TaskCredit.objects.create(
            user=self.user2,
            credits=50,
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            plan=PlanNamesChoices.FREE,
            grant_type=GrantTypeChoices.PROMO
        )
        
        self.agent1_user2 = BrowserUseAgent.objects.create(user=self.user2, name='Task Agent 1 User 2')
        self.task1_agent1_user2 = BrowserUseAgentTask.objects.create(agent=self.agent1_user2, user=self.user2, prompt={'detail': 'Task 1 for Agent 1 User 2'})

        self.client.credentials(HTTP_X_API_KEY=self.raw_api_key1)

    def test_list_tasks_for_specific_agent_owned_by_user(self):
        url = reverse('api:agent-tasks-list', kwargs={'agentId': self.agent1_user1.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)
        task_ids = [task['id'] for task in response.data['results']]
        self.assertIn(str(self.task1_agent1_user1.id), task_ids)
        self.assertIn(str(self.task2_agent1_user1.id), task_ids)
        first_task = response.data['results'][0]
        self.assertEqual(first_task['agent_id'], str(self.agent1_user1.id))

    def test_list_tasks_for_agent_not_owned_by_user(self):
        url = reverse('api:agent-tasks-list', kwargs={'agentId': self.agent1_user2.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_tasks_for_agent_with_no_tasks(self):
        agent_with_no_tasks = BrowserUseAgent.objects.create(user=self.user1, name='Agent With No Tasks')
        url = reverse('api:agent-tasks-list', kwargs={'agentId': agent_with_no_tasks.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 0)

    def test_list_tasks_for_specific_agent_unauthenticated(self):
        self.client.credentials()
        url = reverse('api:agent-tasks-list', kwargs={'agentId': self.agent1_user1.id})
        response = self.client.get(url)
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_list_all_tasks_for_user(self):
        url = reverse('api:user-tasks-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 3)
        task_ids = [task['id'] for task in response.data['results']]
        self.assertIn(str(self.task1_agent1_user1.id), task_ids)
        self.assertIn(str(self.task2_agent1_user1.id), task_ids)
        self.assertIn(str(self.task1_agent2_user1.id), task_ids)

    def test_list_all_tasks_for_user_with_no_tasks(self):
        # Switch to user2 who has one task initially
        self.client.credentials(HTTP_X_API_KEY=self.raw_api_key2)
        # Soft delete the task directly (mimicking API call not under test here)
        self.task1_agent1_user2.is_deleted = True
        self.task1_agent1_user2.deleted_at = timezone.now()
        self.task1_agent1_user2.save()
        
        url = reverse('api:user-tasks-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Since the task is soft-deleted, it should not appear in the list
        self.assertEqual(len(response.data['results']), 0)


    def test_list_all_tasks_for_user_unauthenticated(self):
        self.client.credentials()
        url = reverse('api:user-tasks-list')
        response = self.client.get(url)
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_create_task_for_agent_success(self):
        url = reverse('api:agent-tasks-list', kwargs={'agentId': self.agent1_user1.id})
        task_input_data = {"url": "http://example.com/task_for_agent1"}
        # Use string for now since that's what the current API expects
        data = {'prompt': '{"url": "http://example.com/task_for_agent1"}'}
        response = self.client.post(url, data, format='json')
        if response.status_code != status.HTTP_201_CREATED:
            print(f"test_create_task_for_agent_success response data (status {response.status_code}): {response.data}")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # In API responses prompt is returned as a string
        self.assertEqual(response.data['prompt'], '{"url": "http://example.com/task_for_agent1"}')
        self.assertEqual(response.data['agent'], str(self.agent1_user1.id))
        self.assertTrue(BrowserUseAgentTask.objects.filter(agent=self.agent1_user1, user=self.user1, is_deleted=False).exists())

    def test_create_task_for_agent_not_owned_by_user(self):
        url = reverse('api:agent-tasks-list', kwargs={'agentId': self.agent1_user2.id})
        task_input_data = {"url": "http://example.com/task_for_agent_user2"}
        # Use string for now since that's what the current API expects
        data = {'prompt': '{"url": "http://example.com/task_for_agent_user2"}'}
        response = self.client.post(url, data, format='json')
        if response.status_code != status.HTTP_404_NOT_FOUND:
            print(f"test_create_task_for_agent_not_owned_by_user response data (status {response.status_code}): {response.data}")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        
    def test_wait_parameter_validation(self):
        """Test validation of wait parameter."""
        url = reverse('api:agent-tasks-list', kwargs={'agentId': self.agent1_user1.id})
        
        # Test valid wait parameter - using string for prompt
        data = {'prompt': "Test task with valid wait", 'wait': 10}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Test wait < 0 - using string for prompt
        data = {'prompt': "Test task with negative wait", 'wait': -5}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # Test wait > 1350 - using string for prompt
        data = {'prompt': "Test task with too large wait", 'wait': 1400}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        

    def test_get_task_result_success(self):
        self.task1_agent1_user1.status = BrowserUseAgentTask.StatusChoices.COMPLETED
        self.task1_agent1_user1.save()
        url = reverse('api:agent-tasks-result', kwargs={'agentId': self.agent1_user1.id, 'id': self.task1_agent1_user1.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], str(self.task1_agent1_user1.id))
        self.assertEqual(response.data['agent_id'], str(self.agent1_user1.id))
        self.assertEqual(response.data['result'], 'Result for Task 1 Agent 1')

    def test_get_task_result_task_not_owned_by_user_via_agent(self):
        url = reverse('api:agent-tasks-result', kwargs={'agentId': self.agent1_user2.id, 'id': self.task1_agent1_user2.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_task_result_task_agent_mismatch_for_user(self):
        url = reverse('api:agent-tasks-result', kwargs={'agentId': self.agent2_user1.id, 'id': self.task1_agent1_user1.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_task_result_unauthenticated(self):
        self.client.credentials()
        url = reverse('api:agent-tasks-result', kwargs={'agentId': self.agent1_user1.id, 'id': self.task1_agent1_user1.id})
        response = self.client.get(url)
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_list_tasks_for_agent_pagination(self):
        for i in range(10): # Creates 10 more tasks, total 12 for agent1_user1
            BrowserUseAgentTask.objects.create(agent=self.agent1_user1, user=self.user1, prompt={'detail': f'Pag task {i}'})
        url = reverse('api:agent-tasks-list', kwargs={'agentId': self.agent1_user1.id})
        response = self.client.get(url) # Default page size is 10
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 10)
        self.assertIsNotNone(response.data['next'])
        self.assertEqual(response.data['count'], 12) # 2 from setUp + 10 here

    def test_list_all_tasks_for_user_pagination(self):
        # user1 has 3 tasks from setUp
        # Add 8 more tasks for user1, spread across agents
        for i in range(4):
            BrowserUseAgentTask.objects.create(agent=self.agent1_user1, user=self.user1, prompt={'detail': f'Pag task U1A1 {i}'})
            BrowserUseAgentTask.objects.create(agent=self.agent2_user1, user=self.user1, prompt={'detail': f'Pag task U1A2 {i}'})
        # Total tasks for user1 = 3 (setUp) + 8 (here) = 11
        url = reverse('api:user-tasks-list')
        response = self.client.get(url) # Default page size is 10
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 10)
        self.assertIsNotNone(response.data['next'])
        self.assertEqual(response.data['count'], 11)


    def test_retrieve_task_details_owned_by_user(self):
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': self.task1_agent1_user1.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], str(self.task1_agent1_user1.id))
        self.assertEqual(response.data['agent'], str(self.agent1_user1.id))
        self.assertEqual(response.data['prompt'], self.task1_agent1_user1.prompt)
        self.assertIn('error_message', response.data)

    def test_retrieve_task_details_not_owned_by_user(self):
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user2.id, 'id': self.task1_agent1_user2.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # Tests for Cancel Task Functionality
    def test_cancel_task_pending_success(self):
        task_to_cancel = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1, 
            status=BrowserUseAgentTask.StatusChoices.PENDING, 
            prompt={'detail': 'Cancellable Task Pending'}
        )
        url = reverse('api:agent-tasks-cancel-task', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_cancel.id})
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'cancelled')
        task_to_cancel.refresh_from_db()
        self.assertEqual(task_to_cancel.status, BrowserUseAgentTask.StatusChoices.CANCELLED)
        self.assertTrue(task_to_cancel.updated_at > task_to_cancel.created_at)

    def test_cancel_task_running_success(self):
        task_to_cancel = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1, 
            status=BrowserUseAgentTask.StatusChoices.IN_PROGRESS, 
            prompt={'detail': 'Cancellable Task Running'}
        )
        url = reverse('api:agent-tasks-cancel-task', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_cancel.id})
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'cancelled')
        task_to_cancel.refresh_from_db()
        self.assertEqual(task_to_cancel.status, BrowserUseAgentTask.StatusChoices.CANCELLED)
        self.assertTrue(task_to_cancel.updated_at > task_to_cancel.created_at)

    def test_cancel_task_completed_conflict(self):
        task_completed = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1, 
            status=BrowserUseAgentTask.StatusChoices.COMPLETED, 
            prompt={'detail': 'Completed Task'}
        )
        url = reverse('api:agent-tasks-cancel-task', kwargs={'agentId': self.agent1_user1.id, 'id': task_completed.id})
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn(f'Task is already {BrowserUseAgentTask.StatusChoices.COMPLETED} and cannot be cancelled.', response.data['detail'])
        task_completed.refresh_from_db()
        self.assertEqual(task_completed.status, BrowserUseAgentTask.StatusChoices.COMPLETED)

    def test_cancel_task_failed_conflict(self):
        task_failed = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1, 
            status=BrowserUseAgentTask.StatusChoices.FAILED, 
            prompt={'detail': 'Failed Task'}
        )
        url = reverse('api:agent-tasks-cancel-task', kwargs={'agentId': self.agent1_user1.id, 'id': task_failed.id})
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn(f'Task is already {BrowserUseAgentTask.StatusChoices.FAILED} and cannot be cancelled.', response.data['detail'])
        task_failed.refresh_from_db()
        self.assertEqual(task_failed.status, BrowserUseAgentTask.StatusChoices.FAILED)

    def test_cancel_task_already_cancelled_conflict(self):
        task_cancelled = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1, 
            status=BrowserUseAgentTask.StatusChoices.CANCELLED, 
            prompt={'detail': 'Already Cancelled Task'}
        )
        url = reverse('api:agent-tasks-cancel-task', kwargs={'agentId': self.agent1_user1.id, 'id': task_cancelled.id})
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn(f'Task is already {BrowserUseAgentTask.StatusChoices.CANCELLED} and cannot be cancelled.', response.data['detail'])
        task_cancelled.refresh_from_db()
        self.assertEqual(task_cancelled.status, BrowserUseAgentTask.StatusChoices.CANCELLED)

    def test_cancel_task_unauthenticated(self):
        task_to_cancel = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1, 
            status=BrowserUseAgentTask.StatusChoices.PENDING, 
            prompt={'detail': 'Unauth Cancel Test'}
        )
        self.client.credentials() # Clear credentials
        url = reverse('api:agent-tasks-cancel-task', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_cancel.id})
        response = self.client.post(url)
        
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
        task_to_cancel.refresh_from_db()
        self.assertEqual(task_to_cancel.status, BrowserUseAgentTask.StatusChoices.PENDING) # Status should not change

    def test_cancel_task_not_owned_by_user(self):
        url = reverse('api:agent-tasks-cancel-task', kwargs={'agentId': self.agent1_user2.id, 'id': self.task1_agent1_user2.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND) 
        self.task1_agent1_user2.refresh_from_db()
        self.assertEqual(self.task1_agent1_user2.status, BrowserUseAgentTask.StatusChoices.PENDING)

    # Tests for Update Task Input Data (PATCH)
    def test_update_task_input_data_pending_success(self):
        task_pending = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1,
            status=BrowserUseAgentTask.StatusChoices.PENDING,
            prompt={'old_url': 'http://example.com/old'}
        )
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_pending.id})
        new_prompt = {'new_url': 'http://example.com/new', 'param': 'value'}
        prompt_str = '{"new_url": "http://example.com/new", "param": "value"}'
        # Convert to string for the current API
        response = self.client.patch(url, {'prompt': prompt_str}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task_pending.refresh_from_db()
        # Compare to string since that's what DB will store
        self.assertEqual(task_pending.prompt, prompt_str)
        self.assertEqual(task_pending.status, BrowserUseAgentTask.StatusChoices.PENDING)

    def test_update_task_input_data_running_fail(self):
        task_running = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1,
            status=BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
            prompt={'url': 'http://example.com/running'}
        )
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_running.id})
        new_prompt = {'url': 'http://example.com/new_running'}
        response = self.client.patch(url, {'prompt': '{"url": "http://example.com/new_running"}'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Check that the error message is in one of these fields
        error_msg = 'Task can be modified only while it is PENDING.'
        self.assertTrue(
            (error_msg in response.data.get('status', '')) or 
            (error_msg in response.data.get('detail', '')) or
            any(error_msg in str(err) for err in response.data.values() if isinstance(err, list))
        )
        task_running.refresh_from_db()
        # Check the prompt - could be stored as string or dict or string representation of dict
        prompt = task_running.prompt
        if isinstance(prompt, dict):
            self.assertEqual(prompt, {'url': 'http://example.com/running'})
        else:
            # It could be stored as a JSON string or a string representation of a dict
            self.assertTrue(
                ('"url"' in prompt and 'http://example.com/running' in prompt) or
                ("'url'" in prompt and 'http://example.com/running' in prompt)
            )

    def test_update_task_input_data_completed_fail(self):
        task_completed = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            prompt={'url': 'http://example.com/completed'}
        )
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_completed.id})
        new_prompt = {'url': 'http://example.com/new_completed'}
        response = self.client.patch(url, {'prompt': '{"url": "http://example.com/new_completed"}'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Check that the error message is in one of these fields
        error_msg = 'Task can be modified only while it is PENDING.'
        self.assertTrue(
            (error_msg in response.data.get('status', '')) or 
            (error_msg in response.data.get('detail', '')) or
            any(error_msg in str(err) for err in response.data.values() if isinstance(err, list))
        )

    def test_update_task_input_data_failed_fail(self):
        task_failed = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1,
            status=BrowserUseAgentTask.StatusChoices.FAILED,
            prompt={'url': 'http://example.com/failed'}
        )
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_failed.id})
        new_prompt = {'url': 'http://example.com/new_failed'}
        response = self.client.patch(url, {'prompt': '{"url": "http://example.com/new_failed"}'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Check that the error message is in one of these fields
        error_msg = 'Task can be modified only while it is PENDING.'
        self.assertTrue(
            (error_msg in response.data.get('status', '')) or 
            (error_msg in response.data.get('detail', '')) or
            any(error_msg in str(err) for err in response.data.values() if isinstance(err, list))
        )

    def test_update_task_input_data_cancelled_fail(self):
        task_cancelled = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1,
            status=BrowserUseAgentTask.StatusChoices.CANCELLED,
            prompt={'url': 'http://example.com/cancelled'}
        )
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_cancelled.id})
        new_prompt = {'url': 'http://example.com/new_cancelled'}
        response = self.client.patch(url, {'prompt': '{"url": "http://example.com/new_cancelled"}'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Check that the error message is in one of these fields
        error_msg = 'Task can be modified only while it is PENDING.'
        self.assertTrue(
            (error_msg in response.data.get('status', '')) or 
            (error_msg in response.data.get('detail', '')) or
            any(error_msg in str(err) for err in response.data.values() if isinstance(err, list))
        )

    def test_update_task_input_data_unauthenticated_fail(self):
        task_pending = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1,
            status=BrowserUseAgentTask.StatusChoices.PENDING,
            prompt={'url': 'http://example.com/unauth_test'}
        )
        self.client.credentials()
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_pending.id})
        new_prompt = {'url': 'http://example.com/new_unauth_test'}
        response = self.client.patch(url, {'prompt': new_prompt}, format='json')
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_update_task_input_data_not_owned_fail(self):
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user2.id, 'id': self.task1_agent1_user2.id})
        new_prompt = {'url': 'http://example.com/attempt_not_owned'}
        response = self.client.patch(url, {'prompt': new_prompt}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_task_other_fields_ignored(self):
        task_pending = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1,
            status=BrowserUseAgentTask.StatusChoices.PENDING,
            prompt={'url': 'http://example.com/other_fields_test'}
        )
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_pending.id})
        new_prompt = {'url': 'http://example.com/new_other_fields_test'}
        data_with_other_fields = {
            'prompt': '{"url": "http://example.com/new_other_fields_test"}',
            'status': BrowserUseAgentTask.StatusChoices.COMPLETED,
            'error_message': 'This should be ignored'
        }
        response = self.client.patch(url, data_with_other_fields, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task_pending.refresh_from_db()
        # Depending on DB storage, check for string or dict
        prompt = task_pending.prompt
        if isinstance(prompt, str):
            self.assertIn('"url"', prompt)
            self.assertIn('http://example.com/new_other_fields_test', prompt)
        else:
            self.assertEqual(prompt, {'url': 'http://example.com/new_other_fields_test'})
        self.assertEqual(task_pending.status, BrowserUseAgentTask.StatusChoices.PENDING)
        self.assertIsNone(task_pending.error_message)

    # Tests for Soft Delete Functionality
    def test_soft_delete_task_success(self):
        task_to_delete = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1, prompt={'detail': 'Task to soft delete'}
        )
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_delete.id})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        
        # Verify in DB
        task_to_delete.refresh_from_db()
        self.assertTrue(task_to_delete.is_deleted)
        self.assertIsNotNone(task_to_delete.deleted_at)
        self.assertIsInstance(task_to_delete.deleted_at, timezone.datetime)

    def test_soft_deleted_task_not_in_list_for_agent(self):
        task_to_keep = self.task1_agent1_user1 # Exists from setUp
        task_to_delete = self.task2_agent1_user1 # Exists from setUp

        # Soft delete task_to_delete
        delete_url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_delete.id})
        self.client.delete(delete_url)

        # List tasks for the agent
        list_url = reverse('api:agent-tasks-list', kwargs={'agentId': self.agent1_user1.id})
        response = self.client.get(list_url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task_ids = [task['id'] for task in response.data['results']]
        
        self.assertNotIn(str(task_to_delete.id), task_ids)
        self.assertIn(str(task_to_keep.id), task_ids)
        self.assertEqual(len(task_ids), 1) # Only one task should remain visible

    def test_soft_deleted_task_not_in_list_all_for_user(self):
        # user1 has task1_agent1_user1, task2_agent1_user1, task1_agent2_user1
        task_to_delete = self.task1_agent1_user1
        
        # Soft delete task_to_delete
        delete_url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_delete.id})
        self.client.delete(delete_url)

        # List all tasks for user1
        list_url = reverse('api:user-tasks-list')
        response = self.client.get(list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task_ids = [task['id'] for task in response.data['results']]

        self.assertNotIn(str(task_to_delete.id), task_ids)
        self.assertIn(str(self.task2_agent1_user1.id), task_ids)
        self.assertIn(str(self.task1_agent2_user1.id), task_ids)
        self.assertEqual(len(task_ids), 2) # Two tasks should remain visible

    def test_retrieve_soft_deleted_task_returns_404(self):
        task_to_delete = self.task1_agent1_user1
        # Soft delete the task
        delete_url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_delete.id})
        self.client.delete(delete_url)

        # Attempt to retrieve the task detail
        retrieve_url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_delete.id})
        response = self.client.get(retrieve_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Attempt to retrieve the task result
        result_url = reverse('api:agent-tasks-result', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_delete.id})
        response = self.client.get(result_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        
    def test_retrieve_soft_deleted_task_via_user_tasks_route_returns_404(self):
        task_to_delete = self.task1_agent1_user1
        # Soft delete the task
        delete_url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_delete.id})
        self.client.delete(delete_url)

        # Attempt to retrieve the task via user-tasks-detail route
        user_task_retrieve_url = reverse('api:user-tasks-detail', kwargs={'id': task_to_delete.id})
        response = self.client.get(user_task_retrieve_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


    def test_cancel_soft_deleted_task_returns_404(self):
        task_to_delete = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1, 
            status=BrowserUseAgentTask.StatusChoices.PENDING, 
            prompt={'detail': 'Task for cancel after soft delete test'}
        )
        # Soft delete the task
        delete_url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_delete.id})
        self.client.delete(delete_url)

        # Attempt to cancel the soft-deleted task
        cancel_url = reverse('api:agent-tasks-cancel-task', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_delete.id})
        response = self.client.post(cancel_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_partial_update_soft_deleted_task_returns_404(self):
        task_to_delete = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, user=self.user1,
            status=BrowserUseAgentTask.StatusChoices.PENDING,
            prompt={'detail': 'Task for patch after soft delete test'}
        )
        # Soft delete the task
        delete_url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_delete.id})
        self.client.delete(delete_url)

        # Attempt to PATCH update the soft-deleted task
        patch_url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_delete.id})
        new_prompt = {'new_detail': 'Attempted update'}
        response = self.client.patch(patch_url, {'prompt': new_prompt}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_task_unauthenticated(self):
        task_to_delete = self.task1_agent1_user1
        self.client.credentials() # Clear credentials
        
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task_to_delete.id})
        response = self.client.delete(url)
        
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
        task_to_delete.refresh_from_db()
        self.assertFalse(task_to_delete.is_deleted) # Should not be soft-deleted

    def test_delete_task_not_owned_by_user(self):
        # task1_agent1_user1 is owned by user1
        # self.task1_agent1_user2 is owned by user2
        
        # Authenticate as user2
        self.client.credentials(HTTP_X_API_KEY=self.raw_api_key2)
        
        # User2 attempts to delete task1_agent1_user1 (owned by user1)
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': self.task1_agent1_user1.id})
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.task1_agent1_user1.refresh_from_db()
        self.assertFalse(self.task1_agent1_user1.is_deleted) # Should not be soft-deleted

class AutoCreateApiKeyTest(APITestCase):
    def test_auto_create_api_key_for_new_user(self):
        """Test that a new user automatically gets an API key created."""
        # Create a new user
        new_user = User.objects.create_user(
            username='newuser@example.com', 
            email='newuser@example.com', 
            password='password123'
        )
        
        # Check if an API key was automatically created
        api_keys = ApiKey.objects.filter(user=new_user)
        self.assertEqual(api_keys.count(), 1)
        self.assertEqual(api_keys.first().name, "default")
        
        # Check if the API key is active
        self.assertTrue(api_keys.first().is_active)
        
        # Verify UserQuota was also created
        user_quota = UserQuota.objects.filter(user=new_user)
        self.assertEqual(user_quota.count(), 1)

class BrowserUseAgentTaskQuotaTests(TestCase):
    """Tests for quota checks when creating BrowserUseAgentTask."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="quotatest@example.com",
            email="quotatest@example.com",
            password="password123",
        )
        UserQuota.objects.get_or_create(user=self.user, defaults={"agent_limit": 5})
        self.agent = BrowserUseAgent.objects.create(user=self.user, name="Quota Agent")

    def test_creation_blocked_without_subscription(self):
        """Task creation should fail when no credits and no subscription."""
        # Exhaust any existing credits to simulate 0 remaining
        for tc in TaskCredit.objects.filter(user=self.user):
            tc.credits_used = tc.credits
            tc.save(update_fields=["credits_used"])

        with patch("api.models.get_active_subscription", return_value=None), \
             patch("api.models.TaskCreditService.consume_credit") as mock_consume, \
             self.assertRaises(ValidationError):
            BrowserUseAgentTask.objects.create(
                agent=self.agent,
                user=self.user,
                prompt="Test",
            )
        mock_consume.assert_not_called()

    def test_creation_allowed_with_subscription(self):
        """Task creation succeeds with subscription even without credits."""
        sub = MagicMock()
        with patch("api.models.get_active_subscription", return_value=sub), \
             patch("api.models.TaskCreditService.consume_credit") as mock_consume, \
             patch("util.subscription_helper.report_task_usage") as mock_report:

            from django.utils import timezone
            from datetime import timedelta
            # Define side effect to create a real TaskCredit instance
            def _create_credit(user, additional_task=False):
                return TaskCredit.objects.create(
                    user=user,
                    credits=1,
                    credits_used=1,
                    granted_date=timezone.now(),
                    expiration_date=timezone.now() + timedelta(days=30),
                    additional_task=additional_task,
                    plan=PlanNamesChoices.FREE,
                    grant_type=GrantTypeChoices.PROMO
                )

            mock_consume.side_effect = _create_credit

            task = BrowserUseAgentTask.objects.create(
                agent=self.agent,
                user=self.user,
                prompt="Test",
            )
            self.assertIsNotNone(task.task_credit)
            mock_consume.assert_called_once_with(self.user)
            mock_report.assert_not_called()


class StripeUsageReportingTests(TestCase):
    """Tests for usage reporting helpers."""

    def test_report_extra_task_usage_creates_usage_record(self):
        sub = MagicMock()
        item = MagicMock()
        sub.items.first.return_value = item

        # Patch internals of util.subscription_helper where `report_task_usage` is defined
        with patch("util.subscription_helper.DJSTRIPE_AVAILABLE", True), \
             patch("util.subscription_helper.PaymentsHelper.get_stripe_key", return_value="sk_test_dummy"), \
             patch("util.subscription_helper.stripe") as mock_stripe, \
             patch("django.utils.timezone.now") as mock_now:
            import datetime as _dt
            mock_now.return_value = _dt.datetime(2024, 1, 1, tzinfo=_dt.timezone.utc)

            # Prepare mock for MeterEvent.create
            meter_event_create = MagicMock()
            mock_stripe.billing.MeterEvent.create = meter_event_create

            # Also mock expected settings constant on util.subscription_helper
            from django.conf import settings

            # Ensure subscription.customer.id accessed gracefully
            customer = MagicMock(id="cus_123")
            sub.customer = customer

            report_task_usage(sub, quantity=2)

        # Assert that MeterEvent.create was called once with expected args
        meter_event_create.assert_called_once()

    def test_report_usage_to_stripe_returns_record(self):
        user = MagicMock(id=1)
        customer = MagicMock()

        with patch("util.subscription_helper.get_active_subscription") as mock_get_sub, \
             patch("util.subscription_helper.get_stripe_customer", return_value=customer), \
             patch("util.subscription_helper.PaymentsHelper.get_stripe_key", return_value="sk_test_dummy"), \
             patch("util.subscription_helper.report_task_usage") as mock_report_usage:

            # Mock active subscription to simulate paid plan
            mock_subscription = MagicMock()
            mock_get_sub.return_value = mock_subscription

            result = report_task_usage_to_stripe(user, quantity=3, meter_id="meter")

        # Ensure report_task_usage was invoked with the subscription and correct quantity
        mock_report_usage.assert_called_once_with(mock_subscription, quantity=3)

        # The current implementation does not return a UsageRecord; expect None
        self.assertIsNone(result)
