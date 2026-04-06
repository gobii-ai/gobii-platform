import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, RequestFactory, tag
from django.test.client import Client

from api.models import (
    BrowserUseAgent,
    Organization,
    PersistentAgent,
    PersistentAgentSecret,
)

User = get_user_model()


@tag("batch_global_secrets")
class GlobalSecretModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="gs-model@example.com",
            email="gs-model@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="GSBrowser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="GSAgent",
            charter="Test global secrets",
            browser_use_agent=self.browser_agent,
        )

    def _create_global_credential(self, *, name, domain, value="secret123", user=None):
        secret = PersistentAgentSecret(
            agent=None,
            user=user or self.user,
            organization=None,
            visibility=PersistentAgentSecret.Visibility.GLOBAL,
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern=domain,
            name=name,
            requested=False,
        )
        secret.full_clean()
        secret.set_value(value)
        secret.save()
        return secret

    def _create_global_env_var(self, *, name, value="val123", user=None):
        secret = PersistentAgentSecret(
            agent=None,
            user=user or self.user,
            organization=None,
            visibility=PersistentAgentSecret.Visibility.GLOBAL,
            secret_type=PersistentAgentSecret.SecretType.ENV_VAR,
            domain_pattern=PersistentAgentSecret.ENV_VAR_DOMAIN_SENTINEL,
            name=name,
            requested=False,
        )
        secret.full_clean()
        secret.set_value(value)
        secret.save()
        return secret

    def test_create_global_credential_secret(self):
        secret = self._create_global_credential(name="Global API Key", domain="https://api.example.com")
        self.assertIsNone(secret.agent_id)
        self.assertEqual(secret.user_id, self.user.id)
        self.assertEqual(secret.visibility, PersistentAgentSecret.Visibility.GLOBAL)
        self.assertTrue(secret.key)
        self.assertEqual(secret.get_value(), "secret123")

    def test_create_global_env_var_secret(self):
        secret = self._create_global_env_var(name="MY_API_TOKEN")
        self.assertIsNone(secret.agent_id)
        self.assertEqual(secret.visibility, PersistentAgentSecret.Visibility.GLOBAL)
        self.assertEqual(secret.secret_type, PersistentAgentSecret.SecretType.ENV_VAR)

    def test_global_secret_requires_owner(self):
        secret = PersistentAgentSecret(
            agent=None,
            user=None,
            organization=None,
            visibility=PersistentAgentSecret.Visibility.GLOBAL,
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern="https://example.com",
            name="NoOwner",
        )
        with self.assertRaises(ValidationError):
            secret.full_clean()

    def test_global_secret_rejects_agent(self):
        secret = PersistentAgentSecret(
            agent=self.agent,
            user=self.user,
            visibility=PersistentAgentSecret.Visibility.GLOBAL,
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern="https://example.com",
            name="WithAgent",
        )
        with self.assertRaises(ValidationError):
            secret.full_clean()

    def test_agent_secret_requires_agent(self):
        secret = PersistentAgentSecret(
            agent=None,
            visibility=PersistentAgentSecret.Visibility.AGENT,
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern="https://example.com",
            name="NoAgent",
        )
        with self.assertRaises(ValidationError):
            secret.full_clean()

    def test_global_secret_str_representation(self):
        secret = self._create_global_credential(name="API Key", domain="https://api.example.com")
        self.assertIn("Global Secret", str(secret))

    def test_global_env_var_str_representation(self):
        secret = self._create_global_env_var(name="MY_TOKEN")
        self.assertIn("Global Env Var", str(secret))


@tag("batch_global_secrets")
class GlobalSecretPromptContextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="gs-prompt@example.com",
            email="gs-prompt@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="GSPromptBrowser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="GSPromptAgent",
            charter="Test prompt context",
            browser_use_agent=self.browser_agent,
        )

    def test_get_secrets_block_includes_global_secrets(self):
        from api.agent.core.prompt_context import _get_secrets_block

        # Create a global credential
        gs = PersistentAgentSecret(
            agent=None,
            user=self.user,
            visibility=PersistentAgentSecret.Visibility.GLOBAL,
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern="https://global.example.com",
            name="Global Key",
            requested=False,
        )
        gs.full_clean()
        gs.set_value("globalval")
        gs.save()

        block = _get_secrets_block(self.agent)
        self.assertIn("Global credential secrets", block)
        self.assertIn("Global Key", block)

    def test_get_secrets_block_includes_global_env_vars(self):
        from api.agent.core.prompt_context import _get_secrets_block

        gs = PersistentAgentSecret(
            agent=None,
            user=self.user,
            visibility=PersistentAgentSecret.Visibility.GLOBAL,
            secret_type=PersistentAgentSecret.SecretType.ENV_VAR,
            domain_pattern=PersistentAgentSecret.ENV_VAR_DOMAIN_SENTINEL,
            name="GLOBAL_TOKEN",
            requested=False,
        )
        gs.full_clean()
        gs.set_value("globalenvval")
        gs.save()

        block = _get_secrets_block(self.agent)
        self.assertIn("Global environment variable secrets", block)
        self.assertIn("GLOBAL_TOKEN", block)

    def test_no_secrets_returns_default_message(self):
        from api.agent.core.prompt_context import _get_secrets_block
        block = _get_secrets_block(self.agent)
        self.assertEqual(block, "No secrets configured.")

    def test_agent_and_global_secrets_both_appear(self):
        from api.agent.core.prompt_context import _get_secrets_block

        # Agent secret
        agent_secret = PersistentAgentSecret(
            agent=self.agent,
            visibility=PersistentAgentSecret.Visibility.AGENT,
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern="https://agent.example.com",
            name="Agent Key",
            requested=False,
        )
        agent_secret.full_clean()
        agent_secret.set_value("agentval")
        agent_secret.save()

        # Global secret
        global_secret = PersistentAgentSecret(
            agent=None,
            user=self.user,
            visibility=PersistentAgentSecret.Visibility.GLOBAL,
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern="https://global.example.com",
            name="Global Key",
            requested=False,
        )
        global_secret.full_clean()
        global_secret.set_value("globalval")
        global_secret.save()

        block = _get_secrets_block(self.agent)
        self.assertIn("Agent Key", block)
        self.assertIn("Global Key", block)
        self.assertIn("Global credential secrets", block)
