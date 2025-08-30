"""
Tests for secrets encryption/decryption functionality.
"""
import os
import json
from django.test import TestCase, override_settings, tag
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status
from api.models import BrowserUseAgentTask, ApiKey, TaskCredit
from constants.grant_types import GrantTypeChoices
from api.encryption import SecretsEncryption
from django.utils import timezone
from datetime import timedelta

from constants.plans import PlanNamesChoices

User = get_user_model()


@tag("batch_secrets")
class SecretsEncryptionTest(TestCase):
    """Test the SecretsEncryption class."""
    
    def setUp(self):
        # Save original encryption key if it exists
        self.original_encryption_key = os.environ.get('GOBII_ENCRYPTION_KEY')
        # Set a test encryption key
        os.environ['GOBII_ENCRYPTION_KEY'] = 'test-key-for-encryption-testing-123'
    
    def tearDown(self):
        # Restore original environment variable state
        if self.original_encryption_key is not None:
            os.environ['GOBII_ENCRYPTION_KEY'] = self.original_encryption_key
        elif 'GOBII_ENCRYPTION_KEY' in os.environ:
            del os.environ['GOBII_ENCRYPTION_KEY']
    
    @tag("batch_secrets")
    def test_encrypt_decrypt_roundtrip(self):
        """Test that we can encrypt and decrypt secrets successfully."""
        secrets = {
            'https://example.com': {
                'x_username': 'alice',
                'x_password': 'secret123',
                'x_api_key': 'sk-1234567890abcdef'
            }
        }
        
        # Encrypt
        encrypted = SecretsEncryption.encrypt_secrets(secrets)
        self.assertIsNotNone(encrypted)
        self.assertIsInstance(encrypted, bytes)
        
        # Decrypt
        decrypted = SecretsEncryption.decrypt_secrets(encrypted)
        self.assertEqual(decrypted, secrets)
    
    def test_encrypt_none_returns_none(self):
        """Test that encrypting None returns None."""
        result = SecretsEncryption.encrypt_secrets(None)
        self.assertIsNone(result)
    
    def test_decrypt_none_returns_none(self):
        """Test that decrypting None returns None."""
        result = SecretsEncryption.decrypt_secrets(None)
        self.assertIsNone(result)
    
    def test_encrypt_empty_dict_returns_none(self):
        """Test that encrypting empty dict returns None."""
        result = SecretsEncryption.encrypt_secrets({})
        self.assertIsNone(result)
    
    @tag("batch_secrets")
    def test_missing_encryption_key_raises_error(self):
        """Test that missing encryption key raises ValueError."""
        del os.environ['GOBII_ENCRYPTION_KEY']
        
        with self.assertRaises(ValueError) as cm:
            SecretsEncryption.encrypt_secrets({'https://example.com': {'test': 'value'}})
        
        self.assertIn('GOBII_ENCRYPTION_KEY not configured', str(cm.exception))
    
    def test_legacy_format_rejected_by_default(self):
        """Test that legacy flat format is rejected when allow_legacy=False (default)."""
        legacy_secrets = {
            'x_username': 'alice',
            'x_password': 'secret123'
        }
        
        with self.assertRaises(ValueError) as cm:
            SecretsEncryption.encrypt_secrets(legacy_secrets)
        
        error_message = str(cm.exception)
        self.assertIn('domain-specific format', error_message)
        self.assertIn('https://example.com', error_message)
    
    def test_legacy_format_allowed_with_flag(self):
        """Test that legacy flat format is allowed when allow_legacy=True."""
        legacy_secrets = {
            'x_username': 'alice',
            'x_password': 'secret123'
        }
        
        # Should work with allow_legacy=True
        encrypted = SecretsEncryption.encrypt_secrets(legacy_secrets, allow_legacy=True)
        self.assertIsNotNone(encrypted)
        self.assertIsInstance(encrypted, bytes)
        
        # Decrypt and verify it was converted to domain-specific format
        decrypted = SecretsEncryption.decrypt_secrets(encrypted)
        expected = {
            'https://*.legacy-migrated.local': {
                'x_username': 'alice',
                'x_password': 'secret123'
            }
        }
        self.assertEqual(decrypted, expected)


@override_settings(GOBII_ENCRYPTION_KEY='test-key-for-api-testing-456')
@tag("batch_secrets")
class SecretsAPITest(APITestCase):
    """Test the API endpoints with secrets."""
    
    def setUp(self):
        # Save original encryption key if it exists
        self.original_encryption_key = os.environ.get('GOBII_ENCRYPTION_KEY')
        # Set encryption key in environment for the encryption module
        os.environ['GOBII_ENCRYPTION_KEY'] = 'test-key-for-api-testing-456'
        
        # Create test user and API key
        self.user = User.objects.create_user(
            username='test@example.com',
            email='test@example.com',
            password='testpass123'
        )
        self.api_key_raw, self.api_key_obj = ApiKey.create_for_user(
            user=self.user,
            name='test-key'
        )

        TaskCredit.objects.create(
            user=self.user,
            credits=50,
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            plan=PlanNamesChoices.FREE,
            grant_type=GrantTypeChoices.PROMO
        )

        # Set up API authentication
        self.client.defaults['HTTP_X_API_KEY'] = self.api_key_raw
    
    def tearDown(self):
        # Restore original environment variable state
        if self.original_encryption_key is not None:
            os.environ['GOBII_ENCRYPTION_KEY'] = self.original_encryption_key
        elif 'GOBII_ENCRYPTION_KEY' in os.environ:
            del os.environ['GOBII_ENCRYPTION_KEY']
    
    @tag("batch_secrets")
    def test_create_task_with_secrets(self):
        """Test creating a task with secrets via API."""
        data = {
            'prompt': 'Login using x_username and x_password',
            'secrets': {
                'https://example.com': {
                    'x_username': 'alice',
                    'x_password': 'secret123'
                }
            }
        }
        
        response = self.client.post('/api/v1/tasks/browser-use/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Check that secrets are not in the response
        self.assertNotIn('secrets', response.data)
        
        # Check that task was created with encrypted secrets
        task = BrowserUseAgentTask.objects.get(id=response.data['id'])
        self.assertIsNotNone(task.encrypted_secrets)
        self.assertEqual(task.secret_keys, {'https://example.com': ['x_username', 'x_password']})
        
        # Verify we can decrypt the secrets
        decrypted = SecretsEncryption.decrypt_secrets(task.encrypted_secrets)
        self.assertEqual(decrypted, data['secrets'])
    
    def test_create_task_without_secrets(self):
        """Test creating a task without secrets still works."""
        data = {
            'prompt': 'Visit https://example.com and get the title'
        }
        
        response = self.client.post('/api/v1/tasks/browser-use/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Check that task was created without secrets
        task = BrowserUseAgentTask.objects.get(id=response.data['id'])
        self.assertIsNone(task.encrypted_secrets)
        self.assertIsNone(task.secret_keys)
    
    @tag("batch_secrets")
    def test_invalid_secret_keys_rejected(self):
        """Test that invalid secret keys are rejected."""
        test_cases = [
            {'https://example.com': {'1invalid': 'value'}},  # starts with number
            {'https://example.com': {'invalid-key': 'value'}},  # contains dash
            {'https://example.com': {'invalid key': 'value'}},  # contains space
            {'https://example.com': {'invalid.key': 'value'}},  # contains dot
        ]
        
        for secrets in test_cases:
            data = {
                'prompt': 'Test prompt',
                'secrets': secrets
            }
            
            response = self.client.post('/api/v1/tasks/browser-use/', data, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn('secrets', response.data)
    
    def test_empty_secrets_dict_rejected(self):
        """Test that empty secrets dictionary is rejected."""
        data = {
            'prompt': 'Test prompt',
            'secrets': {}
        }
        
        response = self.client.post('/api/v1/tasks/browser-use/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('secrets', response.data)
    
    def test_non_string_secret_values_rejected(self):
        """Test that non-string secret values are rejected."""
        data = {
            'prompt': 'Test prompt',
            'secrets': {
                'https://example.com': {
                    'x_number': 123,
                    'x_bool': True,
                    'x_list': ['a', 'b']
                }
            }
        }
        
        response = self.client.post('/api/v1/tasks/browser-use/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('secrets', response.data)
    
    def test_legacy_flat_secrets_format_rejected(self):
        """Test that legacy flat secrets format is rejected by the API."""
        data = {
            'prompt': 'Test prompt with x_username and x_password',
            'secrets': {
                'x_username': 'user',
                'x_password': 'pass'
            }
        }
        
        response = self.client.post('/api/v1/tasks/browser-use/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('secrets', response.data)
        # Check that the error message explains the required format
        error_message = str(response.data['secrets'])
        self.assertIn('domain-specific format', error_message)
        self.assertIn('https://example.com', error_message) 
