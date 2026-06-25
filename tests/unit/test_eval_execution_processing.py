import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from litellm.exceptions import APIError

from api.evals.execution import (
    ScenarioExecutionTools,
    get_eval_routing_profile_for_current_run,
    set_current_eval_routing_profile,
    set_current_eval_run_id,
)
from api.models import (
    BrowserUseAgent,
    EvalRun,
    EvalRunTask,
    EvalSuiteRun,
    LLMRoutingProfile,
    LLMProvider,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentWebSession,
    PersistentModelEndpoint,
    build_web_user_address,
)


@tag("batch_eval_fingerprint")
class EvalExecutionProcessingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="eval-execution@example.com",
            email="eval-execution@example.com",
            password="testpass",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Eval Execution Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            browser_use_agent=self.browser_agent,
            name="Eval Execution Agent",
            charter="Test eval processing dispatch.",
        )
        self.tools = ScenarioExecutionTools()

    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.tasks.process_agent_events_task.apply")
    def test_eval_injected_message_processes_agent_inline(self, mock_apply, mock_delay):
        self.tools.inject_message(
            self.agent.id,
            "Fetch this exact URL.",
            trigger_processing=True,
            eval_run_id="00000000-0000-0000-0000-000000000123",
            mock_config={"http_request": {"status": "ok"}},
        )

        mock_apply.assert_called_once_with(
            args=(str(self.agent.id),),
            kwargs={
                "eval_run_id": "00000000-0000-0000-0000-000000000123",
                "mock_config": {"http_request": {"status": "ok"}},
            },
            throw=True,
        )
        mock_delay.assert_not_called()
        message = PersistentAgentMessage.objects.get(owner_agent=self.agent)
        self.assertEqual(
            message.from_endpoint.address,
            build_web_user_address(self.user.id, self.agent.id),
        )
        self.assertTrue(
            PersistentAgentWebSession.objects.filter(
                agent=self.agent,
                user=self.user,
                ended_at__isnull=True,
            ).exists()
        )

    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.tasks.process_agent_events_task.apply")
    def test_non_eval_injected_message_keeps_async_processing(self, mock_apply, mock_delay):
        self.tools.inject_message(
            self.agent.id,
            "Normal user message.",
            trigger_processing=True,
        )

        mock_apply.assert_not_called()
        mock_delay.assert_called_once_with(
            str(self.agent.id),
            eval_run_id=None,
            mock_config=None,
        )

    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.tasks.process_agent_events_task.apply")
    def test_eval_trigger_processing_processes_agent_inline(self, mock_apply, mock_delay):
        self.tools.trigger_processing(
            self.agent.id,
            eval_run_id="00000000-0000-0000-0000-000000000456",
            mock_config={"create_csv": {"status": "ok"}},
        )

        mock_apply.assert_called_once_with(
            args=(str(self.agent.id),),
            kwargs={
                "eval_run_id": "00000000-0000-0000-0000-000000000456",
                "mock_config": {"create_csv": {"status": "ok"}},
            },
            throw=True,
        )
        mock_delay.assert_not_called()

    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.tasks.process_agent_events_task.apply")
    def test_eval_trigger_processing_forwards_stop_policy_when_present(self, mock_apply, mock_delay):
        stop_policy = {"stop_when_all_seen": [{"tool_name": "http_request"}]}

        self.tools.trigger_processing(
            self.agent.id,
            eval_run_id="00000000-0000-0000-0000-000000000789",
            mock_config={"http_request": {"status": "ok"}},
            eval_stop_policy=stop_policy,
        )

        mock_apply.assert_called_once_with(
            args=(str(self.agent.id),),
            kwargs={
                "eval_run_id": "00000000-0000-0000-0000-000000000789",
                "mock_config": {"http_request": {"status": "ok"}},
                "eval_stop_policy": stop_policy,
            },
            throw=True,
        )
        mock_delay.assert_not_called()

    @patch("api.agent.core.llm_config.get_llm_config_with_failover")
    @patch("api.evals.execution.run_completion")
    def test_llm_judge_falls_back_to_json_when_judgment_tool_missing(self, mock_completion, mock_configs):
        mock_configs.return_value = [("test_provider", "test-model", {})]
        mock_completion.side_effect = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]))]),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                {"choice": "Yes", "reasoning": "The condition is satisfied."}
                            )
                        )
                    )
                ]
            ),
        ]

        choice, reasoning = self.tools.llm_judge(
            question="Does the answer contain the requested weather?",
            context="The answer says 72F and Sunny.",
        )

        self.assertEqual(choice, "Yes")
        self.assertIn("Structured judge fallback used", reasoning)
        self.assertIn("The condition is satisfied.", reasoning)
        self.assertEqual(mock_completion.call_count, 2)
        self.assertEqual(
            mock_completion.call_args_list[0].kwargs["tool_choice"],
            {"type": "function", "function": {"name": "submit_judgment"}},
        )
        self.assertNotIn("tools", mock_completion.call_args_list[1].kwargs)
        self.assertNotIn("tool_choice", mock_completion.call_args_list[1].kwargs)

    @patch("api.agent.core.llm_config.get_llm_config_with_failover")
    @patch("api.evals.execution.run_completion")
    def test_llm_judge_falls_back_to_json_on_structured_grammar_error(self, mock_completion, mock_configs):
        mock_configs.return_value = [("openrouter", "deepseek/deepseek-v4-flash", {})]
        grammar_error = APIError(
            status_code=500,
            message=(
                "OpenrouterException - Upstream error from Morph: "
                "Failed to compile structural_tag grammar"
            ),
            llm_provider="openrouter",
            model="deepseek/deepseek-v4-flash",
        )
        mock_completion.side_effect = [
            grammar_error,
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                {"choice": "Pass", "reasoning": "The required skill was enabled and used."}
                            )
                        )
                    )
                ]
            ),
        ]

        choice, reasoning = self.tools.llm_judge(
            question="Did the agent correctly execute the selected global skill?",
            context="The selected global skill was enabled and an effective tool was used.",
            options=("Pass", "Fail"),
        )

        self.assertEqual(choice, "Pass")
        self.assertIn("Structured judge fallback used", reasoning)
        self.assertEqual(mock_completion.call_count, 2)
        self.assertIn("tools", mock_completion.call_args_list[0].kwargs)
        self.assertNotIn("tools", mock_completion.call_args_list[1].kwargs)
        self.assertNotIn("tool_choice", mock_completion.call_args_list[1].kwargs)

    @patch("api.evals.execution.run_completion")
    def test_llm_judge_prefers_eval_judge_endpoint_from_snapshot(self, mock_completion):
        provider = LLMProvider.objects.create(
            key="eval-openai-compat",
            display_name="Eval OpenAI Compat",
            browser_backend=LLMProvider.BrowserBackend.OPENAI_COMPAT,
        )
        endpoint = PersistentModelEndpoint.objects.create(
            key="eval_judge_model",
            provider=provider,
            litellm_model="judge-model",
            api_base="https://judge.example.test/v1",
            supports_tool_choice=True,
        )
        profile = LLMRoutingProfile.objects.create(
            name="eval-judge-snapshot",
            display_name="Eval Judge Snapshot",
            is_eval_snapshot=True,
            eval_judge_endpoint=endpoint,
        )
        suite_run = EvalSuiteRun.objects.create(
            suite_slug="debug_suite",
            initiated_by=self.user,
            llm_routing_profile=profile,
        )
        run = EvalRun.objects.create(
            suite_run=suite_run,
            scenario_slug="debug_artifact_test",
            agent=self.agent,
            initiated_by=self.user,
            status=EvalRun.Status.RUNNING,
        )
        judgment_call = SimpleNamespace(
            function=SimpleNamespace(
                name="submit_judgment",
                arguments=json.dumps({"choice": "Yes", "reasoning": "The endpoint was used."}),
            )
        )
        mock_completion.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[judgment_call]))]
        )

        set_current_eval_run_id(str(run.id))
        try:
            choice, reasoning = self.tools.llm_judge(
                question="Was the snapshot judge endpoint used?",
                context="Endpoint-specific config should be preserved.",
                params={"temperature": 0.0, "max_tokens": 42},
            )
        finally:
            set_current_eval_run_id(None)

        self.assertEqual(choice, "Yes")
        self.assertEqual(reasoning, "The endpoint was used.")
        kwargs = mock_completion.call_args.kwargs
        self.assertEqual(kwargs["model"], "openai/judge-model")
        self.assertEqual(kwargs["params"]["api_base"], "https://judge.example.test/v1")
        self.assertEqual(kwargs["params"]["api_key"], "sk-noauth")
        self.assertEqual(kwargs["params"]["temperature"], 0.0)
        self.assertEqual(kwargs["params"]["max_tokens"], 42)

    def test_record_task_result_persists_sanitized_debug_artifacts(self):
        run = EvalRun.objects.create(
            scenario_slug="debug_artifact_test",
            agent=self.agent,
            initiated_by=self.user,
            status=EvalRun.Status.RUNNING,
        )
        task = EvalRunTask.objects.create(
            run=run,
            sequence=1,
            name="verify_tool_params",
            assertion_type="manual",
        )

        self.tools.record_task_result(
            str(run.id),
            None,
            EvalRunTask.Status.FAILED,
            task_name=task.name,
            observed_summary="Tool params did not match.",
            artifacts={
                "params": {"url": "https://example.test/data.json", "api_key": "must-not-persist"},
                "judge_context": {"question": "Was the request correct?", "answer": "No"},
                "messages": ["short transcript summary"],
            },
        )

        task.refresh_from_db()
        self.assertEqual(task.debug_artifacts["params"]["url"], "https://example.test/data.json")
        self.assertNotIn("api_key", task.debug_artifacts["params"])
        self.assertEqual(task.debug_artifacts["judge_context"]["answer"], "No")
        self.assertEqual(task.debug_artifacts["messages"], ["short transcript summary"])

    def test_eval_judge_routing_profile_falls_back_to_persisted_run_context(self):
        profile = LLMRoutingProfile.objects.create(
            name="eval-judge-profile",
            display_name="Eval Judge Profile",
        )
        suite_run = EvalSuiteRun.objects.create(
            suite_slug="debug_suite",
            initiated_by=self.user,
            llm_routing_profile=profile,
        )
        run = EvalRun.objects.create(
            suite_run=suite_run,
            scenario_slug="debug_artifact_test",
            agent=self.agent,
            initiated_by=self.user,
            status=EvalRun.Status.RUNNING,
        )

        set_current_eval_routing_profile(None)
        set_current_eval_run_id(str(run.id))
        try:
            resolved = get_eval_routing_profile_for_current_run()
        finally:
            set_current_eval_run_id(None)

        self.assertEqual(resolved, profile)
