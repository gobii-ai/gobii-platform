import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from api.agent.core.prompt_context import build_prompt_context_preview
from api.models import (
    BrowserUseAgent,
    LLMProvider,
    PersistentAgent,
    PersistentAgentPromptArchive,
    PersistentAgentSystemMessage,
    PersistentModelEndpoint,
)


User = get_user_model()


def _completion_response(content="done", *, prompt_tokens=100, completion_tokens=20):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            prompt_tokens_details=SimpleNamespace(cached_tokens=5),
        ),
        cost_details=SimpleNamespace(
            prompt_cost=0.001,
            completion_cost=0.002,
            total_cost=0.003,
        ),
    )


@tag("llm_routing_profiles_batch")
class ConsoleLLMPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            username="llm-perf-admin",
            email="llm-perf-admin@example.com",
            password="password123",
            is_staff=True,
        )
        cls.regular_user = User.objects.create_user(
            username="llm-perf-user",
            email="llm-perf-user@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(
            user=cls.staff_user,
            name="Performance Browser Agent",
        )
        cls.agent = PersistentAgent.objects.create(
            user=cls.staff_user,
            name="Performance Agent",
            charter="Answer benchmark prompts.",
            browser_use_agent=cls.browser_agent,
        )
        cls.provider = LLMProvider.objects.create(
            key="perf-provider",
            display_name="Performance Provider",
            enabled=True,
            env_var_name="PERF_PROVIDER_KEY",
        )
        cls.endpoint = PersistentModelEndpoint.objects.create(
            key="perf-endpoint",
            provider=cls.provider,
            litellm_model="perf-model",
            enabled=True,
        )
        cls.second_endpoint = PersistentModelEndpoint.objects.create(
            key="perf-endpoint-two",
            provider=cls.provider,
            litellm_model="perf-model-two",
            enabled=True,
        )

    def setUp(self):
        self.url = reverse("console_llm_performance_test")

    def _payload(self, **overrides):
        payload = {
            "endpoint_id": str(self.endpoint.id),
            "sample_number": 1,
            "input_token_size": 120000,
        }
        payload.update(overrides)
        return json.dumps(payload)

    def test_non_staff_users_receive_403(self):
        self.client.force_login(self.regular_user)
        response = self.client.post(self.url, self._payload(), content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_llm_config_page_moved_to_staff_only_top_level_route(self):
        page_url = reverse("llm-config")

        self.client.force_login(self.regular_user)
        response = self.client.get(page_url)
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.staff_user)
        response = self.client.get(page_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-app="llm-config"')
        self.assertNotContains(response, "console-mcp-servers")

        response = self.client.get("/console/llm-config/")
        self.assertEqual(response.status_code, 404)

    def test_evals_pages_moved_to_staff_only_top_level_routes(self):
        page_url = reverse("evals")
        detail_url = reverse("evals-detail", args=[self.agent.id])

        self.client.force_login(self.regular_user)
        response = self.client.get(page_url)
        self.assertEqual(response.status_code, 403)
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.staff_user)
        response = self.client.get(page_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-app="evals"')
        self.assertNotContains(response, "console-mcp-servers")

        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-app="evals-detail"')
        self.assertNotContains(response, "console-mcp-servers")

        response = self.client.get("/console/evals/")
        self.assertEqual(response.status_code, 404)
        response = self.client.get(f"/console/evals/{self.agent.id}/")
        self.assertEqual(response.status_code, 404)

    def test_rejects_missing_endpoint(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            self._payload(endpoint_id=""),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("endpoint_id is required", response.content.decode("utf-8"))

    def test_rejects_unknown_endpoint(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            self._payload(endpoint_id="00000000-0000-0000-0000-000000000000"),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_rejects_disabled_endpoint(self):
        self.client.force_login(self.staff_user)
        self.endpoint.enabled = False
        self.endpoint.save(update_fields=["enabled"])
        response = self.client.post(self.url, self._payload(), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Endpoint is disabled", response.json()["message"])

    def test_rejects_disabled_provider(self):
        self.client.force_login(self.staff_user)
        self.provider.enabled = False
        self.provider.save(update_fields=["enabled"])
        response = self.client.post(self.url, self._payload(), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Provider is disabled", response.json()["message"])

    def test_rejects_sample_bounds(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            self._payload(sample_number=11),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("sample_number must be between 1 and 10", response.content.decode("utf-8"))

    def test_rejects_invalid_input_token_size(self):
        self.client.force_login(self.staff_user)
        cases = [
            ("large", "input_token_size must be an integer"),
            (1000, "input_token_size must be one of 10000, 60000, 120000"),
        ]
        for input_token_size, message in cases:
            with self.subTest(input_token_size=input_token_size):
                response = self.client.post(
                    self.url,
                    self._payload(input_token_size=input_token_size),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.content.decode("utf-8"))

    def test_successful_run_uses_synthetic_messages_and_returns_single_sample(self):
        self.client.force_login(self.staff_user)
        with (
            patch.dict("os.environ", {"PERF_PROVIDER_KEY": "test-key"}),
            patch("api.agent.tools.sqlite_agent_config.seed_sqlite_agent_config") as mock_seed_config,
            patch("api.agent.tools.sqlite_skills.seed_sqlite_skills") as mock_seed_skills,
            patch("api.agent.core.prompt_context.get_agent_tools") as mock_get_tools,
            patch("api.agent.core.prompt_context.build_prompt_context_preview") as mock_prompt_preview,
            patch("console.api_views.litellm.token_counter", side_effect=lambda model, text: max(1, len(text.split()))),
            patch("console.api_views.run_completion", return_value=_completion_response("one", prompt_tokens=120, completion_tokens=12)) as mock_run_completion,
        ):
            response = self.client.post(
                self.url,
                self._payload(input_token_size=10000, sample_number=3),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["endpoint"]["id"], str(self.endpoint.id))
        self.assertEqual(payload["input_size"]["requested_input_tokens"], 10000)
        self.assertGreaterEqual(payload["input_size"]["estimated_prompt_tokens"], 1)
        self.assertEqual(payload["input_size"]["message_count"], 2)
        self.assertEqual(payload["sample"]["sample"], 3)
        self.assertEqual(payload["sample"]["input_cost_total"], 0.001)
        self.assertEqual(payload["sample"]["output_cost"], 0.002)
        self.assertEqual(payload["sample"]["total_cost"], 0.003)
        self.assertEqual(mock_run_completion.call_count, 1)
        call = mock_run_completion.call_args
        self.assertNotIn("tools", call.kwargs)
        self.assertIn("Synthetic benchmark context follows", call.kwargs["messages"][1]["content"])
        mock_seed_config.assert_not_called()
        mock_seed_skills.assert_not_called()
        mock_get_tools.assert_not_called()
        mock_prompt_preview.assert_not_called()

    def test_provider_sample_failure_returns_failed_sample(self):
        self.client.force_login(self.staff_user)
        with (
            patch.dict("os.environ", {"PERF_PROVIDER_KEY": "test-key"}),
            patch("console.api_views.litellm.token_counter", side_effect=lambda model, text: max(1, len(text.split()))),
            patch("console.api_views.run_completion") as mock_run_completion,
        ):
            mock_run_completion.side_effect = [
                RuntimeError("provider unavailable"),
            ]
            response = self.client.post(
                self.url,
                self._payload(input_token_size=60000),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["input_size"]["requested_input_tokens"], 60000)
        self.assertFalse(payload["sample"]["ok"])
        self.assertIn("provider unavailable", payload["sample"]["error"])

    @patch("api.agent.core.prompt_context._safe_get_prompt_failover_configs", return_value=[("provider", "model", {})])
    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=False)
    @patch("api.agent.core.prompt_context._get_sandbox_prompt_summary", return_value="")
    @patch("api.agent.core.prompt_context.ensure_steps_compacted")
    @patch("api.agent.core.prompt_context.ensure_comms_compacted")
    @patch("api.agent.core.prompt_context.add_budget_awareness_sections")
    def test_prompt_preview_does_not_archive_or_consume_system_messages(
        self,
        mock_budget_sections,
        mock_ensure_comms,
        mock_ensure_steps,
        _mock_sandbox_summary,
        _mock_custom_tools_sandbox,
        _mock_configs,
    ):
        system_message = PersistentAgentSystemMessage.objects.create(
            agent=self.agent,
            body="Temporary directive",
            is_active=True,
        )
        before_archive_count = PersistentAgentPromptArchive.objects.count()

        messages, token_count, metadata = build_prompt_context_preview(
            self.agent,
            daily_credit_state={},
        )

        system_message.refresh_from_db()
        self.assertTrue(system_message.is_active)
        self.assertEqual(PersistentAgentPromptArchive.objects.count(), before_archive_count)
        self.assertGreaterEqual(len(messages), 2)
        self.assertGreater(token_count, 0)
        self.assertIn("prompt_failover_configs", metadata)
        mock_ensure_steps.assert_not_called()
        mock_ensure_comms.assert_not_called()
        mock_budget_sections.assert_called()
