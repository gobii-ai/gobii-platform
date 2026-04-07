import json
import os
from contextlib import ExitStack
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from waffle.models import Flag

from api.models import BrowserUseAgent, GlobalSecret, Organization, OrganizationMembership, PersistentAgent, PersistentAgentSecret


User = get_user_model()


def _ensure_encryption_key():
    if not os.environ.get("GOBII_ENCRYPTION_KEY"):
        os.environ["GOBII_ENCRYPTION_KEY"] = "test-key-for-encryption-testing-123"


def _create_console_test_agent(*, user, organization=None, name: str) -> PersistentAgent:
    with ExitStack() as stack:
        stack.enter_context(patch.object(BrowserUseAgent, "select_random_proxy", return_value=None))
        if organization is not None:
            stack.enter_context(patch.object(PersistentAgent, "_validate_org_seats", return_value=None))
        browser = BrowserUseAgent.objects.create(user=user, name=f"{name}-browser")
        return PersistentAgent.objects.create(
            user=user,
            organization=organization,
            name=name,
            charter="",
            browser_use_agent=browser,
        )


@tag("global_secrets_batch")
@override_settings(
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
)
class ConsoleSecretsTests(TestCase):
    def setUp(self):
        _ensure_encryption_key()
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})

        self.user = User.objects.create_user(
            username="secrets-user",
            email="secrets@example.com",
            password="test-pass-123",
        )
        self.org = Organization.objects.create(
            name="Acme Corp",
            slug="acme-corp",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.org_agent = _create_console_test_agent(user=self.user, organization=self.org, name="Org Secrets Agent")
        self.client.force_login(self.user)

    def _set_org_context(self):
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session["context_name"] = self.org.name
        session.save()

    def _make_global_secret(self, **kwargs):
        defaults = {
            "user": self.user,
            "name": "Console Global Secret",
            "secret_type": GlobalSecret.SecretType.CREDENTIAL,
            "domain_pattern": "https://example.com",
            "description": "global secret",
        }
        defaults.update(kwargs)
        secret = GlobalSecret(**defaults)
        secret.set_value("supersecret")
        secret.save()
        return secret

    def _make_agent_secret(self, **kwargs):
        defaults = {
            "agent": self.org_agent,
            "name": "Console Agent Secret",
            "secret_type": PersistentAgentSecret.SecretType.CREDENTIAL,
            "domain_pattern": "https://example.com",
            "description": "agent secret",
        }
        defaults.update(kwargs)
        secret = PersistentAgentSecret(**defaults)
        secret.set_value("supersecret")
        secret.save()
        return secret

    def test_global_secrets_page_uses_personal_scope_mount_attributes(self):
        response = self.client.get(reverse("console-secrets"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-owner-scope="user"')
        self.assertNotContains(response, "data-owner-label=")

    def test_global_secrets_page_uses_organization_scope_mount_attributes(self):
        self._set_org_context()

        response = self.client.get(reverse("console-secrets"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-owner-scope="organization"')
        self.assertNotContains(response, "data-owner-label=")

    def test_create_global_secret_returns_created(self):
        response = self.client.post(
            reverse("console-global-secret-list"),
            data=json.dumps(
                {
                    "name": "API Key",
                    "secret_type": "credential",
                    "domain_pattern": "https://example.com",
                    "value": "top-secret",
                    "description": "Created through API",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["message"], "Global secret created.")
        self.assertTrue(GlobalSecret.objects.filter(user=self.user, name="API Key").exists())

    def test_delete_global_secret_returns_ok(self):
        secret = self._make_global_secret()

        response = self.client.delete(reverse("console-global-secret-detail", args=[secret.id]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(GlobalSecret.objects.filter(id=secret.id).exists())

    def test_create_agent_secret_returns_created(self):
        self._set_org_context()

        response = self.client.post(
            reverse("console-agent-secret-list", args=[self.org_agent.id]),
            data=json.dumps(
                {
                    "name": "Database Password",
                    "secret_type": "credential",
                    "domain_pattern": "https://db.example.com",
                    "value": "top-secret",
                    "description": "Created for an agent",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["message"], "Agent secret created.")
        self.assertTrue(
            PersistentAgentSecret.objects.filter(agent=self.org_agent, name="Database Password").exists()
        )

    def test_delete_agent_secret_returns_ok(self):
        secret = self._make_agent_secret()
        self._set_org_context()

        response = self.client.delete(reverse("console-agent-secret-detail", args=[self.org_agent.id, secret.id]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersistentAgentSecret.objects.filter(id=secret.id).exists())

    def test_promote_agent_secret_returns_created_and_moves_secret(self):
        secret = self._make_agent_secret(name="Promote Me")
        self._set_org_context()

        response = self.client.post(reverse("console-agent-secret-promote", args=[self.org_agent.id, secret.id]))

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["message"], "Secret promoted to global.")
        self.assertFalse(PersistentAgentSecret.objects.filter(id=secret.id).exists())
        promoted = GlobalSecret.objects.get(organization=self.org, name="Promote Me")
        self.assertEqual(payload["secret"]["id"], str(promoted.id))
