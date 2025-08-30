import uuid
from django.test import TestCase, tag
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from rest_framework import status, serializers
from django.urls import reverse
from jsonschema import ValidationError as JSValidationError, SchemaError
from api.models import BrowserUseAgent, ApiKey, BrowserUseAgentTask, UserQuota, TaskCredit
from constants.grant_types import GrantTypeChoices
from django.utils import timezone
from datetime import timedelta
from api.serializers import BrowserUseAgentTaskSerializer
from constants.plans import PlanNamesChoices

User = get_user_model()

class OutputSchemaValidationTests(TestCase):
    """Tests for validating the JSON Schema in the BrowserUseAgentTaskSerializer."""
    
    def setUp(self):
        self.serializer = BrowserUseAgentTaskSerializer()
        
    @tag("batch_output_schema")
    def test_valid_schema(self):
        """Test that a valid JSON Schema passes validation."""
        valid_schema = {
            "title": "HackerNewsPosts",
            "type": "object",
            "properties": {
                "posts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                            "points": {"type": "integer"}
                        },
                        "required": ["title", "url"]
                    }
                }
            },
            "required": ["posts"]
        }
        
        # Should not raise any exceptions
        result = self.serializer.validate_output_schema(valid_schema)
        self.assertEqual(result, valid_schema)
        
    @tag("batch_output_schema")
    def test_invalid_schema_type(self):
        """Test that an invalid schema type is rejected."""
        invalid_schema = {
            "type": "invalid_type",  # Invalid type
            "properties": {
                "name": {"type": "string"}
            }
        }
        
        # The validation method will catch the SchemaError and 
        # re-raise it as a DRF ValidationError
        with self.assertRaises((serializers.ValidationError, SchemaError)):
            self.serializer.validate_output_schema(invalid_schema)
    
    @tag("batch_output_schema")
    def test_schema_too_deep(self):
        """Test that a schema with excessive nesting is rejected."""
        # Create a deeply nested schema
        schema = {"type": "object", "properties": {}}
        current = schema["properties"]
        
        # Create 45 levels of nesting (more than the 40 limit, but not excessive)
        for i in range(45):
            current["nested"] = {"type": "object", "properties": {}}
            current = current["nested"]["properties"]
        
        with self.assertRaises(serializers.ValidationError) as context:
            self.serializer.validate_output_schema(schema)
            
        # The serializer should raise a validation error about schema depth
        self.assertIn("Schema too deep", str(context.exception))
    
    def test_schema_too_many_properties(self):
        """Test that a schema with too many properties is rejected."""
        # Create a schema with lots of properties
        properties = {f"prop{i}": {"type": "string"} for i in range(2100)}  # Just over the 2000 limit
        schema = {
            "type": "object",
            "properties": properties
        }
        
        with self.assertRaises(serializers.ValidationError) as context:
            self.serializer.validate_output_schema(schema)
            
        # The serializer should raise a validation error about property count
        self.assertIn("Schema too complex", str(context.exception))


class OutputSchemaAPITests(APITestCase):
    """Tests for the API endpoints with output_schema functionality."""
    
    def setUp(self):
        # User 1
        self.user1 = User.objects.create_user(username='user1@example.com', email='user1@example.com', password='password123')
        UserQuota.objects.get_or_create(user=self.user1, defaults={'agent_limit': 5})
        self.raw_api_key1, self.api_key_obj1 = ApiKey.create_for_user(self.user1, name='test_key1')

        TaskCredit.objects.create(
            user=self.user1,
            credits=50,
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            plan=PlanNamesChoices.FREE,
            grant_type=GrantTypeChoices.PROMO
        )
        
        # Create an agent for User 1
        self.agent1_user1 = BrowserUseAgent.objects.create(user=self.user1, name='Agent 1 User 1')
        
        # Authenticate as user1 by default
        self.client.credentials(HTTP_X_API_KEY=self.raw_api_key1)
        
        # Valid schema for testing
        self.valid_schema = {
            "title": "HackerNewsPosts",
            "type": "object",
            "properties": {
                "posts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                            "points": {"type": "integer"}
                        },
                        "required": ["title", "url"]
                    }
                }
            },
            "required": ["posts"]
        }
        
        # Invalid schema for testing
        self.invalid_schema = {
            # Incorrect - string properties should have type "string" not "str"
            "type": "object",
            "properties": {
                "name": {"type": "str"} 
            }
        }
    
    @tag("batch_output_schema")
    def test_create_task_with_valid_schema(self):
        """Test creating a task with a valid output schema."""
        url = reverse('api:agent-tasks-list', kwargs={'agentId': self.agent1_user1.id})
        data = {
            'prompt': "Go to news.ycombinator.com and get top 5 posts",  # String is expected
            'output_schema': self.valid_schema
        }
        
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['output_schema'], self.valid_schema)
        
        # Verify in database
        task_id = response.data['id']
        task = BrowserUseAgentTask.objects.get(id=task_id)
        self.assertEqual(task.output_schema, self.valid_schema)
    
    def test_create_task_with_invalid_schema(self):
        """Test creating a task with an invalid output schema."""
        # Use a schema with valid JSON Schema structure but validation constraints that will fail 
        invalid_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": -1}  # Invalid minLength (must be >= 0)
            }
        }
        
        url = reverse('api:agent-tasks-list', kwargs={'agentId': self.agent1_user1.id})
        data = {
            'prompt': "Go to news.ycombinator.com and get top 5 posts",  # String is expected
            'output_schema': invalid_schema
        }
        
        # The serializer should return a 400 Bad Request
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_update_task_schema_when_pending(self):
        """Test updating a task's output schema when it's in PENDING state."""
        # Create a task first
        task = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, 
            user=self.user1,
            status=BrowserUseAgentTask.StatusChoices.PENDING,
            prompt="Go to news.ycombinator.com",
            output_schema=None  # No schema initially
        )
        
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task.id})
        data = {'output_schema': self.valid_schema}
        
        response = self.client.patch(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify in database
        task.refresh_from_db()
        self.assertEqual(task.output_schema, self.valid_schema)
    
    def test_update_task_schema_when_not_pending(self):
        """Test updating a task's output schema when it's not in PENDING state."""
        # Create a task in IN_PROGRESS state
        task = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, 
            user=self.user1,
            status=BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
            prompt="Go to news.ycombinator.com",
            output_schema=None  # No schema initially
        )
        
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task.id})
        data = {'output_schema': self.valid_schema}
        
        response = self.client.patch(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('status', response.data)
        
        # Verify schema hasn't changed in database
        task.refresh_from_db()
        self.assertIsNone(task.output_schema)
    
    def test_read_task_with_schema(self):
        """Test retrieving a task with an output schema."""
        # Create a task with a schema
        task = BrowserUseAgentTask.objects.create(
            agent=self.agent1_user1, 
            user=self.user1,
            prompt="Go to news.ycombinator.com",
            output_schema=self.valid_schema
        )
        
        url = reverse('api:agent-tasks-detail', kwargs={'agentId': self.agent1_user1.id, 'id': task.id})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['output_schema'], self.valid_schema)
