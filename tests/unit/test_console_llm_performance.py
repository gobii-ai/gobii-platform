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
            "agent_id": str(self.agent.id),
            "endpoint_ids": [str(self.endpoint.id)],
            "samples_per_endpoint": 2,
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

    def test_performance_agent_search_only_returns_eligible_agents(self):
        deleted_agent = PersistentAgent.objects.create(
            user=self.staff_user,
            name="Performance Deleted Agent",
            charter="Deleted benchmark prompt.",
            browser_use_agent=BrowserUseAgent.objects.create(
                user=self.staff_user,
                name="Performance Deleted Browser Agent",
            ),
            is_deleted=True,
        )
        eval_agent = PersistentAgent.objects.create(
            user=self.staff_user,
            name="Performance Eval Agent",
            charter="Eval benchmark prompt.",
            browser_use_agent=BrowserUseAgent.objects.create(
                user=self.staff_user,
                name="Performance Eval Browser Agent",
            ),
            execution_environment="eval",
        )

        self.client.force_login(self.staff_user)
        url = reverse("console_agent_search")

        response = self.client.get(url, {"q": "Performance", "eligible_for": "llm_performance"})
        self.assertEqual(response.status_code, 200)
        agent_ids = {agent["id"] for agent in response.json()["agents"]}
        self.assertIn(str(self.agent.id), agent_ids)
        self.assertNotIn(str(deleted_agent.id), agent_ids)
        self.assertNotIn(str(eval_agent.id), agent_ids)

        response = self.client.get(url, {"q": str(deleted_agent.id)})
        self.assertEqual(response.status_code, 200)
        self.assertIn(str(deleted_agent.id), {agent["id"] for agent in response.json()["agents"]})

    def test_rejects_invalid_agent(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            self._payload(agent_id="00000000-0000-0000-0000-000000000000"),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_rejects_missing_endpoints(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            self.url,
            self._payload(endpoint_ids=[]),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Select at least one endpoint", response.content.decode("utf-8"))

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
            self._payload(samples_per_endpoint=11),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("samples_per_endpoint must be between 1 and 10", response.content.decode("utf-8"))

    @patch.dict("os.environ", {"PERF_PROVIDER_KEY": "test-key"})
    @patch("console.api_views.seed_sqlite_agent_config")
    @patch("console.api_views.seed_sqlite_skills")
    @patch("console.api_views.get_agent_tools", return_value=[{"type": "function", "function": {"name": "noop", "parameters": {}}}])
    @patch("console.api_views.build_prompt_context_preview")
    @patch("console.api_views.run_completion")
    def test_successful_run_includes_tools_and_serial_samples(
        self,
        mock_run_completion,
        mock_prompt_preview,
        mock_get_tools,
        _mock_seed_skills,
        _mock_seed_config,
    ):
        self.client.force_login(self.staff_user)
        mock_prompt_preview.return_value = (
            [{"role": "system", "content": "system"}, {"role": "user", "content": "user"}],
            123,
            {"prompt_allows_implied_send": True},
        )
        mock_run_completion.side_effect = [
            _completion_response("one", prompt_tokens=120, completion_tokens=12),
            _completion_response("two", prompt_tokens=121, completion_tokens=13),
            _completion_response("three", prompt_tokens=122, completion_tokens=14),
            _completion_response("four", prompt_tokens=123, completion_tokens=15),
        ]

        response = self.client.post(
            self.url,
            self._payload(endpoint_ids=[str(self.endpoint.id), str(self.second_endpoint.id)]),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["prompt"]["tokens"], 123)
        self.assertEqual(payload["prompt"]["tool_count"], 1)
        self.assertEqual(len(payload["endpoints"]), 2)
        self.assertEqual(mock_run_completion.call_count, 4)
        for call in mock_run_completion.call_args_list:
            self.assertEqual(call.kwargs["tools"], mock_get_tools.return_value)
        self.assertEqual(payload["endpoints"][0]["summary"]["success_count"], 2)
        self.assertEqual(payload["endpoints"][0]["summary"]["error_count"], 0)
        self.assertEqual(payload["endpoints"][0]["samples"][0]["input_cost_total"], 0.001)
        self.assertEqual(payload["endpoints"][0]["samples"][0]["output_cost"], 0.002)
        self.assertEqual(payload["endpoints"][0]["samples"][0]["total_cost"], 0.003)
        self.assertEqual(payload["endpoints"][0]["summary"]["total_input_cost"], 0.002)
        self.assertEqual(payload["endpoints"][0]["summary"]["total_output_cost"], 0.004)
        self.assertEqual(payload["endpoints"][0]["summary"]["total_cost"], 0.006)

    @patch.dict("os.environ", {"PERF_PROVIDER_KEY": "test-key"})
    @patch("console.api_views.seed_sqlite_agent_config")
    @patch("console.api_views.seed_sqlite_skills")
    @patch("console.api_views.get_agent_tools", return_value=[])
    @patch("console.api_views.build_prompt_context_preview")
    @patch("console.api_views.run_completion")
    def test_sample_failure_does_not_abort_endpoint(
        self,
        mock_run_completion,
        mock_prompt_preview,
        _mock_get_tools,
        _mock_seed_skills,
        _mock_seed_config,
    ):
        self.client.force_login(self.staff_user)
        mock_prompt_preview.return_value = (
            [{"role": "system", "content": "system"}, {"role": "user", "content": "user"}],
            50,
            {},
        )
        mock_run_completion.side_effect = [
            RuntimeError("provider unavailable"),
            _completion_response("recovered", prompt_tokens=60, completion_tokens=10),
        ]

        response = self.client.post(self.url, self._payload(), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        endpoint_payload = response.json()["endpoints"][0]
        self.assertEqual(endpoint_payload["summary"]["success_count"], 1)
        self.assertEqual(endpoint_payload["summary"]["error_count"], 1)
        self.assertFalse(endpoint_payload["samples"][0]["ok"])
        self.assertIn("provider unavailable", endpoint_payload["samples"][0]["error"])

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
