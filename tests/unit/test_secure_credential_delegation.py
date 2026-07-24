import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from api.agent.core.prompt_context import _get_system_instruction
from api.agent.system_skills import get_system_skill_definition, shortlist_system_skills
from api.agent.tools.http_request import get_http_request_tool
from api.agent.tools.meta_gobii import execute_meta_gobii_tool
from api.agent.tools.meta_gobii_names import META_GOBII_SYSTEM_SKILL_KEY
from api.agent.tools.secure_api_request import (
    SECURE_API_REQUEST_TOOL_NAME,
    SECURE_CREDENTIAL_DELEGATION_SYSTEM_SKILL_KEY,
    execute_secure_api_request,
)
from api.models import (
    AgentEmailAccount,
    BrowserUseAgent,
    DelegatedSecureValue,
    PersistentAgent,
    PersistentAgentSecret,
    PersistentAgentSystemSkillState,
)
from api.services.delegated_secure_values import create_delegated_secure_value


@tag("batch_agent_tools")
class SecureApiRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="secure-api-owner",
            email="secure-api-owner@example.com",
            password="secret",
        )
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Credential Manager",
            charter="Provision accounts securely.",
            browser_use_agent=BrowserUseAgent.objects.create(
                user=cls.user,
                name="Credential Manager Browser",
            ),
        )

    @patch("api.agent.tools.secure_api_request.execute_http_request")
    def test_extracts_public_fields_and_returns_only_encrypted_references(self, mock_http):
        mock_http.return_value = {
            "status": "ok",
            "status_code": 200,
            "headers": {"Set-Cookie": "private-cookie"},
            "content": {
                "results": [
                    {
                        "id": "mailbox-1",
                        "address": "worker@example.com",
                        "appPassword": "google-app-password",
                        "password": "login-password",
                    }
                ]
            },
        }

        result = execute_secure_api_request(
            self.agent,
            {
                "method": "GET",
                "url": "https://mailboxes.example.test/v1/mailboxes",
                "headers": {"Authorization": "$[secret:provider_api_key]"},
                "collection_pointer": "/results",
                "public_fields": {"mailbox_id": "/id", "address": "/address"},
                "secret_fields": {
                    "app_password": "/appPassword",
                    "login_password": "/password",
                },
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["mailbox_id"], "mailbox-1")
        self.assertEqual(result["items"][0]["address"], "worker@example.com")
        refs = result["items"][0]["secure_values"]
        self.assertRegex(refs["app_password"], r"^sv_[0-9a-f-]{36}$")
        self.assertRegex(refs["login_password"], r"^sv_[0-9a-f-]{36}$")
        serialized_result = json.dumps(result)
        self.assertNotIn("google-app-password", serialized_result)
        self.assertNotIn("login-password", serialized_result)
        self.assertNotIn("private-cookie", serialized_result)

        stored = list(DelegatedSecureValue.objects.filter(source_agent=self.agent))
        self.assertEqual(len(stored), 2)
        self.assertEqual({value.get_value() for value in stored}, {"google-app-password", "login-password"})
        self.assertTrue(all(b"password" not in bytes(value.encrypted_value) for value in stored))

    @patch("api.agent.tools.secure_api_request.execute_http_request")
    def test_rejects_sensitive_public_mapping_before_request(self, mock_http):
        result = execute_secure_api_request(
            self.agent,
            {
                "method": "GET",
                "url": "https://api.example.test/accounts",
                "public_fields": {"initial_password": "/initialPassword"},
                "secret_fields": {"credential": "/password"},
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("secret_fields", result["message"])
        mock_http.assert_not_called()

    @patch("api.agent.tools.secure_api_request.execute_http_request")
    def test_sanitizes_provider_failure_without_exposing_response(self, mock_http):
        mock_http.return_value = {
            "status": "ok",
            "status_code": 401,
            "content": {"error": "bad key", "password": "leaked-response-password"},
        }

        result = execute_secure_api_request(
            self.agent,
            {
                "method": "GET",
                "url": "https://api.example.test/accounts",
                "public_fields": {"id": "/id"},
                "secret_fields": {"credential": "/password"},
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["status_code"], 401)
        self.assertNotIn("leaked-response-password", json.dumps(result))
        self.assertFalse(DelegatedSecureValue.objects.filter(source_agent=self.agent).exists())

    def test_system_skill_is_generic_and_discoverable(self):
        definition = get_system_skill_definition(SECURE_CREDENTIAL_DELEGATION_SYSTEM_SKILL_KEY)

        self.assertIsNotNone(definition)
        self.assertEqual(definition.tool_names, (SECURE_API_REQUEST_TOOL_NAME,))
        self.assertIn("never fetch that response", definition.prompt_instructions.lower())
        self.assertIn(
            "never install a mailbox credential as a generic agent secret",
            definition.prompt_instructions.lower(),
        )
        self.assertNotIn("primeforge", definition.prompt_instructions.lower())
        matches = shortlist_system_skills(
            "provision credentials from an API into several child Gobiis",
            available_tool_names={SECURE_API_REQUEST_TOOL_NAME},
        )
        self.assertEqual([match.skill_key for match in matches], [SECURE_CREDENTIAL_DELEGATION_SYSTEM_SKILL_KEY])

    def test_ordinary_http_directs_credential_responses_to_secure_delegation(self):
        description = get_http_request_tool()["function"]["description"].lower()

        self.assertIn("response may contain credentials", description)
        self.assertIn("secure credential delegation", description)

    def test_core_api_routing_exempts_credential_bearing_responses(self):
        prompt = _get_system_instruction(self.agent, is_first_run=True)

        self.assertIn(
            "credential-returning API -> search_tools('secure credential delegation') first",
            prompt,
        )
        self.assertIn("non-secret data/api/feed/file URL -> http_request", prompt)

    def test_real_harness_eval_suite_is_registered(self):
        import api.evals.loader  # noqa: F401
        from api.evals.registry import ScenarioRegistry
        from api.evals.scenarios.secure_credential_delegation import (
            SECURE_CREDENTIAL_DELEGATION_SCENARIO_SLUGS,
            SECURE_CREDENTIAL_DELEGATION_SUITE_SLUG,
        )
        from api.evals.suites import SuiteRegistry

        suite = SuiteRegistry.get(SECURE_CREDENTIAL_DELEGATION_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), SECURE_CREDENTIAL_DELEGATION_SCENARIO_SLUGS)
        for slug in SECURE_CREDENTIAL_DELEGATION_SCENARIO_SLUGS:
            scenario = ScenarioRegistry.get(slug)
            self.assertIsNotNone(scenario)
            self.assertFalse(scenario.supports_simulation)


@tag("batch_agent_tools")
class SecureValueMetaGobiiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="secure-meta-owner",
            email="secure-meta-owner@example.com",
            password="secret",
        )
        cls.other_user = User.objects.create_user(
            username="secure-meta-other",
            email="secure-meta-other@example.com",
            password="secret",
        )
        cls.manager = PersistentAgent.objects.create(
            user=cls.user,
            name="Manager",
            charter="Manage workers.",
            browser_use_agent=BrowserUseAgent.objects.create(user=cls.user, name="Manager Browser"),
        )
        cls.other_manager = PersistentAgent.objects.create(
            user=cls.user,
            name="Other Manager",
            charter="Manage separate workers.",
            browser_use_agent=BrowserUseAgent.objects.create(user=cls.user, name="Other Manager Browser"),
        )
        cls.worker = PersistentAgent.objects.create(
            user=cls.user,
            name="Worker",
            charter="Do assigned work.",
            browser_use_agent=BrowserUseAgent.objects.create(user=cls.user, name="Worker Browser"),
        )
        cls.second_worker = PersistentAgent.objects.create(
            user=cls.user,
            name="Second Worker",
            charter="Do other work.",
            browser_use_agent=BrowserUseAgent.objects.create(user=cls.user, name="Second Worker Browser"),
        )
        cls.outsider = PersistentAgent.objects.create(
            user=cls.other_user,
            name="Outsider",
            charter="Outside owner scope.",
            browser_use_agent=BrowserUseAgent.objects.create(user=cls.other_user, name="Outsider Browser"),
        )
        PersistentAgentSystemSkillState.objects.create(
            agent=cls.manager,
            skill_key=META_GOBII_SYSTEM_SKILL_KEY,
            is_enabled=True,
        )
        PersistentAgentSystemSkillState.objects.create(
            agent=cls.other_manager,
            skill_key=META_GOBII_SYSTEM_SKILL_KEY,
            is_enabled=True,
        )

    def test_assigns_reference_to_child_secret_once_and_allows_idempotent_retry(self):
        secure_ref = create_delegated_secure_value(
            self.manager,
            label="crm_token",
            value="super-secret-token",
        )
        params = {
            "agent_id": str(self.worker.id),
            "secure_value_ref": secure_ref,
            "domain_pattern": "https://api.example.com",
            "name": "CRM token",
            "key": "crm_token",
            "user_confirmed": True,
        }

        result = execute_meta_gobii_tool(self.manager, "meta_gobii_assign_agent_secret", params)
        retry = execute_meta_gobii_tool(self.manager, "meta_gobii_assign_agent_secret", params)

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["already_applied"])
        self.assertEqual(retry["status"], "ok")
        self.assertTrue(retry["already_applied"])
        secret = PersistentAgentSecret.objects.get(agent=self.worker, key="crm_token")
        self.assertEqual(secret.get_value(), "super-secret-token")
        self.assertNotIn("super-secret-token", json.dumps(result))

        wrong_target = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_assign_agent_secret",
            {**params, "agent_id": str(self.second_worker.id)},
        )
        self.assertEqual(wrong_target["status"], "error")
        self.assertIn("already been consumed", wrong_target["message"])

    def test_rejects_expired_reference_and_inaccessible_target(self):
        expired_ref = create_delegated_secure_value(
            self.manager,
            label="expired",
            value="expired-value",
        )
        DelegatedSecureValue.objects.filter(source_agent=self.manager).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        params = {
            "agent_id": str(self.worker.id),
            "secure_value_ref": expired_ref,
            "domain_pattern": "https://api.example.com",
            "name": "Expired",
            "key": "expired",
            "user_confirmed": True,
        }

        expired = execute_meta_gobii_tool(self.manager, "meta_gobii_assign_agent_secret", params)
        inaccessible = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_assign_agent_secret",
            {**params, "agent_id": str(self.outsider.id)},
        )

        self.assertEqual(expired["status"], "error")
        self.assertIn("expired", expired["message"])
        self.assertEqual(inaccessible["status"], "error")
        self.assertIn("inaccessible", inaccessible["message"])

    def test_reference_is_scoped_to_the_manager_that_created_it(self):
        secure_ref = create_delegated_secure_value(
            self.manager,
            label="scoped_token",
            value="manager-only-token",
        )

        result = execute_meta_gobii_tool(
            self.other_manager,
            "meta_gobii_assign_agent_secret",
            {
                "agent_id": str(self.worker.id),
                "secure_value_ref": secure_ref,
                "domain_pattern": "https://api.example.com",
                "name": "Scoped token",
                "key": "scoped_token",
                "user_confirmed": True,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("not found or is not accessible", result["message"])
        self.assertFalse(
            PersistentAgentSecret.objects.filter(
                agent=self.worker,
                key="scoped_token",
            ).exists()
        )

    @override_settings(PUBLIC_SITE_URL="https://app.gobii.test")
    @patch("api.services.agent_email_provisioning.validate_agent_imap_connection", return_value=(True, ""))
    @patch("api.services.agent_email_provisioning.validate_agent_smtp_connection", return_value=(True, ""))
    def test_configures_google_app_password_email_end_to_end(self, _smtp, _imap):
        secure_ref = create_delegated_secure_value(
            self.manager,
            label="app_password",
            value="google-app-password",
        )

        result = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_configure_agent_email",
            {
                "agent_id": str(self.worker.id),
                "email_address": "worker@customer.example",
                "connection_mode": "custom",
                "provider": "gmail",
                "secure_value_ref": secure_ref,
                "enable_outbound": True,
                "enable_inbound": True,
                "user_confirmed": True,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["outbound_enabled"])
        self.assertTrue(result["inbound_enabled"])
        self.assertNotIn("google-app-password", json.dumps(result))
        account = AgentEmailAccount.objects.get(endpoint__owner_agent=self.worker, endpoint__address="worker@customer.example")
        self.assertEqual(account.smtp_host, "smtp.gmail.com")
        self.assertEqual(account.imap_host, "imap.gmail.com")
        self.assertEqual(account.get_smtp_password(), "google-app-password")
        self.assertEqual(account.get_imap_password(), "google-app-password")
        self.assertTrue(account.is_outbound_enabled)
        self.assertTrue(account.is_inbound_enabled)

    @patch("api.services.agent_email_provisioning.validate_agent_imap_connection", return_value=(False, "blocked"))
    @patch("api.services.agent_email_provisioning.validate_agent_smtp_connection", return_value=(True, ""))
    def test_keeps_passing_email_direction_enabled_when_other_direction_fails(self, _smtp, _imap):
        secure_ref = create_delegated_secure_value(
            self.manager,
            label="app_password",
            value="google-app-password",
        )

        result = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_configure_agent_email",
            {
                "agent_id": str(self.worker.id),
                "email_address": "partial@customer.example",
                "connection_mode": "custom",
                "provider": "gmail",
                "secure_value_ref": secure_ref,
                "enable_outbound": True,
                "enable_inbound": True,
                "user_confirmed": True,
            },
        )

        self.assertEqual(result["status"], "needs_attention")
        self.assertTrue(result["outbound_enabled"])
        self.assertFalse(result["inbound_enabled"])
        account = AgentEmailAccount.objects.get(
            endpoint__owner_agent=self.worker,
            endpoint__address="partial@customer.example",
        )
        self.assertTrue(account.is_outbound_enabled)
        self.assertFalse(account.is_inbound_enabled)
        self.assertIsNotNone(account.connection_last_ok_at)

    @override_settings(PUBLIC_SITE_URL="https://app.gobii.test")
    def test_prepares_microsoft_oauth_without_accepting_plaintext(self):
        result = execute_meta_gobii_tool(
            self.manager,
            "meta_gobii_configure_agent_email",
            {
                "agent_id": str(self.worker.id),
                "email_address": "worker@customer.example",
                "connection_mode": "oauth2",
                "provider": "microsoft",
                "user_confirmed": True,
            },
        )

        self.assertEqual(result["status"], "oauth_required")
        self.assertEqual(
            result["oauth_setup_url"],
            f"https://app.gobii.test/app/agents/{self.worker.id}/email",
        )
        account = AgentEmailAccount.objects.get(endpoint__owner_agent=self.worker, endpoint__address="worker@customer.example")
        self.assertEqual(account.smtp_host, "smtp.office365.com")
        self.assertEqual(account.imap_host, "outlook.office365.com")
        self.assertEqual(account.connection_mode, AgentEmailAccount.ConnectionMode.OAUTH2)
        self.assertFalse(account.is_outbound_enabled)
        self.assertFalse(account.is_inbound_enabled)
