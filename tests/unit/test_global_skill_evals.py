import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.evals.scenarios.global_skill_eval import GlobalSkillEvalScenario, _score_skill_execution
from api.evals.global_skill_evals import (
    GLOBAL_SKILL_EVAL_SCENARIO_SLUG,
    GLOBAL_SKILL_EVAL_SUITE_SLUG,
)
from api.evals.owner import EVAL_RUNNER_ORG_SLUG, EVAL_RUNNER_USERNAME
from api.models import (
    EvalRun,
    EvalSuiteRun,
    GlobalAgentSkill,
    GlobalSecret,
)


class _FakeQuerySet:
    def __init__(self, items):
        self._items = list(items)

    def order_by(self, *args, **kwargs):
        return self

    def select_related(self, *args, **kwargs):
        return self

    def first(self):
        return self._items[0] if self._items else None

    def __getitem__(self, key):
        return self._items[key]


@tag("batch_global_skill_evals")
class GlobalSkillEvalAPITests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="eval-admin",
            email="eval-admin@example.com",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)

    def _create_global_secret(self, *, name: str, key: str, secret_type: str, domain_pattern: str, value: str):
        secret = GlobalSecret(
            user=self.user,
            name=name,
            key=key,
            secret_type=secret_type,
            domain_pattern=domain_pattern,
            description="",
        )
        secret.set_value(value)
        secret.save()
        return secret

    def test_launcher_lists_skill_readiness(self):
        skill = GlobalAgentSkill.objects.create(
            name="check-weather",
            description="Check weather with a dedicated skill.",
            tools=["weather"],
            secrets=[
                {
                    "name": "Weather API key",
                    "key": "WEATHER_API_KEY",
                    "secret_type": "env_var",
                    "description": "API key",
                },
                {
                    "name": "Weather portal login",
                    "key": "portal_password",
                    "secret_type": "credential",
                    "description": "Portal login",
                    "domain_pattern": "https://weather.example.com",
                },
            ],
            instructions="Use this skill for weather tasks.",
            is_active=True,
        )
        self._create_global_secret(
            name="Weather API key",
            key="WEATHER_API_KEY",
            secret_type=GlobalSecret.SecretType.ENV_VAR,
            domain_pattern=GlobalSecret.ENV_VAR_DOMAIN_SENTINEL,
            value="secret-value",
        )

        response = self.client.get(reverse("console_evals_global_skill_launcher"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["rubric_version"], "v1")
        self.assertEqual(payload["global_secrets_url"], "/app/secrets")
        self.assertEqual(len(payload["global_skills"]), 1)
        skill_payload = payload["global_skills"][0]
        self.assertEqual(skill_payload["id"], str(skill.id))
        self.assertEqual(skill_payload["effective_tool_ids"], ["weather"])
        self.assertFalse(skill_payload["launchable"])
        self.assertEqual(
            skill_payload["missing_required_secrets"],
            ["Weather portal login [credential:portal_password @ https://weather.example.com]"],
        )

    @patch("console.api_views.gc_eval_runs_task.delay")
    @patch("console.api_views.run_eval_task.delay")
    def test_create_skill_eval_run_persists_suite_run_metadata(self, mock_run_eval_delay, mock_gc_delay):
        skill = GlobalAgentSkill.objects.create(
            name="check-weather",
            description="Check weather with a dedicated skill.",
            tools=["weather"],
            secrets=[
                {
                    "name": "Weather API key",
                    "key": "WEATHER_API_KEY",
                    "secret_type": "env_var",
                    "description": "API key",
                },
            ],
            instructions="Use this skill for weather tasks.",
            is_active=True,
        )
        self._create_global_secret(
            name="Weather API key",
            key="WEATHER_API_KEY",
            secret_type=GlobalSecret.SecretType.ENV_VAR,
            domain_pattern=GlobalSecret.ENV_VAR_DOMAIN_SENTINEL,
            value="secret-value",
        )

        response = self.client.post(
            reverse("console_evals_global_skill_runs_create"),
            data={
                "global_skill_id": str(skill.id),
                "task_prompt": "Get the current weather in Boston and summarize it.",
                "n_runs": 2,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        suite_run = EvalSuiteRun.objects.get()
        self.assertEqual(suite_run.suite_slug, GLOBAL_SKILL_EVAL_SUITE_SLUG)
        self.assertEqual(suite_run.launcher_type, EvalSuiteRun.LauncherType.GLOBAL_SKILL)
        self.assertEqual(suite_run.agent_strategy, EvalSuiteRun.AgentStrategy.EPHEMERAL_PER_SCENARIO)
        self.assertEqual(suite_run.run_type, EvalSuiteRun.RunType.ONE_OFF)
        self.assertEqual(suite_run.launch_config["global_skill_id"], str(skill.id))
        self.assertEqual(suite_run.launch_config["global_skill_name"], "check-weather")
        self.assertEqual(suite_run.launch_config["task_prompt"], "Get the current weather in Boston and summarize it.")
        self.assertEqual(suite_run.launch_config["effective_tool_ids"], ["weather"])
        self.assertEqual(EvalRun.objects.count(), 2)
        runs = list(EvalRun.objects.select_related("agent__user", "agent__organization").order_by("created_at"))
        self.assertTrue(all(run.scenario_slug == GLOBAL_SKILL_EVAL_SCENARIO_SLUG for run in runs))
        self.assertTrue(all(run.initiated_by == self.user for run in runs))
        self.assertTrue(all(run.agent.user.username == EVAL_RUNNER_USERNAME for run in runs))
        self.assertTrue(all(run.agent.organization.slug == EVAL_RUNNER_ORG_SLUG for run in runs))
        self.assertTrue(all(run.agent.execution_environment == "eval" for run in runs))
        self.assertGreaterEqual(runs[0].agent.organization.billing.purchased_seats, 2)
        self.assertEqual(mock_run_eval_delay.call_count, 2)
        mock_gc_delay.assert_called_once()

        response_payload = response.json()
        self.assertEqual(response_payload["suite_runs"][0]["display_name"], "check-weather")
        self.assertEqual(response_payload["suite_runs"][0]["launcher_type"], "global_skill")
        self.assertEqual(response_payload["suite_runs"][0]["skill_eval"]["global_skill_name"], "check-weather")

        list_response = self.client.get(reverse("console_evals_suite_runs"))
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()["suite_runs"][0]
        self.assertEqual(list_payload["display_name"], "check-weather")
        self.assertEqual(list_payload["skill_eval"]["task_prompt"], "Get the current weather in Boston and summarize it.")

        detail_response = self.client.get(reverse("console_evals_suite_run_detail", args=[suite_run.id]))
        self.assertEqual(detail_response.status_code, 200)
        detail_payload = detail_response.json()["suite_run"]
        self.assertEqual(detail_payload["display_name"], "check-weather")
        self.assertEqual(detail_payload["skill_eval"]["rubric_version"], "v1")

    @patch("console.api_views.gc_eval_runs_task.delay")
    @patch("console.api_views.run_eval_task.delay")
    def test_create_skill_eval_defaults_to_single_run(self, mock_run_eval_delay, mock_gc_delay):
        skill = GlobalAgentSkill.objects.create(
            name="check-weather",
            description="Check weather with a dedicated skill.",
            tools=["weather"],
            secrets=[
                {
                    "name": "Weather API key",
                    "key": "WEATHER_API_KEY",
                    "secret_type": "env_var",
                    "description": "API key",
                },
            ],
            instructions="Use this skill for weather tasks.",
            is_active=True,
        )
        self._create_global_secret(
            name="Weather API key",
            key="WEATHER_API_KEY",
            secret_type=GlobalSecret.SecretType.ENV_VAR,
            domain_pattern=GlobalSecret.ENV_VAR_DOMAIN_SENTINEL,
            value="secret-value",
        )

        response = self.client.post(
            reverse("console_evals_global_skill_runs_create"),
            data={
                "global_skill_id": str(skill.id),
                "task_prompt": "Get the current weather in Boston and summarize it.",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        suite_run = EvalSuiteRun.objects.get()
        self.assertEqual(suite_run.requested_runs, 1)
        self.assertEqual(EvalRun.objects.count(), 1)
        mock_run_eval_delay.assert_called_once()
        mock_gc_delay.assert_called_once()

    def test_create_skill_eval_rejects_missing_task_prompt(self):
        skill = GlobalAgentSkill.objects.create(
            name="check-weather",
            description="Check weather with a dedicated skill.",
            tools=["weather"],
            instructions="Use this skill for weather tasks.",
            is_active=True,
        )

        response = self.client.post(
            reverse("console_evals_global_skill_runs_create"),
            data={"global_skill_id": str(skill.id), "task_prompt": "   "},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("task_prompt is required", response.content.decode("utf-8"))

    def test_create_skill_eval_rejects_missing_required_secret(self):
        skill = GlobalAgentSkill.objects.create(
            name="check-weather",
            description="Check weather with a dedicated skill.",
            tools=["weather"],
            secrets=[
                {
                    "name": "Weather API key",
                    "key": "WEATHER_API_KEY",
                    "secret_type": "env_var",
                    "description": "API key",
                },
            ],
            instructions="Use this skill for weather tasks.",
            is_active=True,
        )

        response = self.client.post(
            reverse("console_evals_global_skill_runs_create"),
            data={"global_skill_id": str(skill.id), "task_prompt": "Get the weather."},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["missing_required_secrets"], ["Weather API key [env_var:WEATHER_API_KEY]"])

    def test_create_skill_eval_rejects_inactive_skill(self):
        skill = GlobalAgentSkill.objects.create(
            name="retired-skill",
            description="Inactive skill.",
            tools=["weather"],
            instructions="Use this skill for weather tasks.",
            is_active=False,
        )

        response = self.client.post(
            reverse("console_evals_global_skill_runs_create"),
            data={"global_skill_id": str(skill.id), "task_prompt": "Get the weather."},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)

    def test_create_skill_eval_rejects_malformed_global_skill_id(self):
        response = self.client.post(
            reverse("console_evals_global_skill_runs_create"),
            data={"global_skill_id": "not-a-uuid", "task_prompt": "Get the weather."},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("global_skill_id must be a valid UUID", response.content.decode("utf-8"))

    def test_create_skill_eval_rejects_malformed_routing_profile_id(self):
        skill = GlobalAgentSkill.objects.create(
            name="check-weather",
            description="Check weather with a dedicated skill.",
            tools=["weather"],
            instructions="Use this skill for weather tasks.",
            is_active=True,
        )

        response = self.client.post(
            reverse("console_evals_global_skill_runs_create"),
            data={
                "global_skill_id": str(skill.id),
                "task_prompt": "Get the weather.",
                "llm_routing_profile_id": "not-a-uuid",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("LLM routing profile not found", response.content.decode("utf-8"))


@tag("batch_global_skill_evals")
class GlobalSkillEvalScenarioTests(TestCase):
    def _run_scenario(
        self,
        *,
        enabled_skill,
        relevant_tool_calls,
        judge_result,
        post_prompt_calls=None,
    ):
        now = timezone.now()
        launch_config = {
            "global_skill_id": "skill-1",
            "global_skill_name": "check-weather",
            "task_prompt": "Get the current weather in Boston and summarize it.",
            "effective_tool_ids": ["weather"],
            "required_secret_status": [],
        }
        scenario = GlobalSkillEvalScenario()
        recorded = []

        skill = SimpleNamespace(
            description="Weather helper",
            instructions="Use weather tools.",
            get_effective_tool_ids=lambda: ["weather"],
        )

        all_post_prompt_calls = post_prompt_calls if post_prompt_calls is not None else (relevant_tool_calls or [])
        final_response = SimpleNamespace(body="It is 70F and sunny in Boston.")
        judge_calls = []

        def fake_llm_judge(**kwargs):
            judge_calls.append(kwargs)
            return judge_result

        with (
            patch.object(scenario, "get_run", return_value=SimpleNamespace(suite_run=SimpleNamespace(launch_config=launch_config))),
            patch.object(scenario, "wait_for_agent_idle", return_value=nullcontext()),
            patch.object(scenario, "inject_message", return_value=SimpleNamespace(timestamp=now)),
            patch.object(scenario, "record_task_result", side_effect=lambda *args, **kwargs: recorded.append({"task_name": kwargs.get("task_name"), "status": args[2], "observed_summary": kwargs.get("observed_summary", ""), "artifacts": kwargs.get("artifacts", {})})),
            patch.object(scenario, "llm_judge", side_effect=fake_llm_judge),
            patch("api.evals.scenarios.global_skill_eval.GlobalAgentSkill.objects.filter") as mock_skill_filter,
            patch("api.evals.scenarios.global_skill_eval.PersistentAgentSkill.objects.filter") as mock_enabled_filter,
            patch("api.evals.scenarios.global_skill_eval.PersistentAgentToolCall.objects.filter") as mock_tool_filter,
            patch("api.evals.scenarios.global_skill_eval.PersistentAgentMessage.objects.filter") as mock_message_filter,
        ):
            mock_skill_filter.return_value.first.return_value = skill
            mock_enabled_filter.return_value = _FakeQuerySet([enabled_skill] if enabled_skill else [])
            mock_tool_filter.side_effect = [
                _FakeQuerySet(relevant_tool_calls),
                _FakeQuerySet(all_post_prompt_calls),
            ]
            mock_message_filter.return_value = _FakeQuerySet([final_response])

            scenario.run("run-1", "agent-1")

        self.last_judge_calls = judge_calls
        return recorded

    def test_scenario_passes_when_skill_enabled_tool_used_and_judge_passes(self):
        tool_call = SimpleNamespace(
            tool_name="weather",
            tool_params={"location": "Boston"},
            status="complete",
            step=SimpleNamespace(created_at=timezone.now()),
        )

        recorded = self._run_scenario(
            enabled_skill=SimpleNamespace(name="check-weather"),
            relevant_tool_calls=[tool_call],
            judge_result=("Pass", "The agent enabled the skill, used its weather tool, and answered correctly."),
        )

        final_status_by_task = {}
        for item in recorded:
            final_status_by_task[item["task_name"]] = item["status"]
        self.assertEqual(
            final_status_by_task,
            {
                "inject_skill_task": "passed",
                "verify_skill_enabled": "passed",
                "verify_skill_tool_usage": "passed",
                "judge_skill_execution": "passed",
            },
        )

    def test_execution_context_separates_allowed_utility_calls_from_skill_calls(self):
        now = timezone.now()
        weather_call = SimpleNamespace(
            tool_name="weather",
            tool_params={"location": "Boston"},
            status="complete",
            step=SimpleNamespace(created_at=now),
        )
        post_prompt_calls = [
            SimpleNamespace(tool_name="search_tools", tool_params={"query": "check-weather"}, status="complete", step=SimpleNamespace(created_at=now)),
            weather_call,
            SimpleNamespace(tool_name="sqlite_batch", tool_params={"sql": "SELECT result_text FROM __tool_results"}, status="complete", step=SimpleNamespace(created_at=now)),
            SimpleNamespace(tool_name="send_chat_message", tool_params={"body": "It is sunny."}, status="complete", step=SimpleNamespace(created_at=now)),
        ]

        recorded = self._run_scenario(
            enabled_skill=SimpleNamespace(name="check-weather"),
            relevant_tool_calls=[weather_call],
            post_prompt_calls=post_prompt_calls,
            judge_result=("Pass", "Allowed utility calls did not replace the skill tool."),
        )

        context = recorded[-1]["artifacts"]["execution_context"]
        self.assertEqual([call["tool_name"] for call in context["skill_tool_calls"]], ["weather"])
        self.assertEqual(
            [call["tool_name"] for call in context["allowed_utility_tool_calls"]],
            ["search_tools", "sqlite_batch", "send_chat_message"],
        )
        self.assertEqual(context["other_non_skill_tool_calls"], [])
        self.assertEqual(self.last_judge_calls, [])

    def test_deterministic_skill_execution_ignores_judge_weather_plausibility(self):
        passed, reasoning = _score_skill_execution(
            enabled_skill_detected=True,
            relevant_tool_call_detected=True,
            other_non_skill_tool_calls=[],
            final_response="72F and Sunny",
        )

        self.assertTrue(passed, reasoning)

    def test_scenario_uses_default_skill_fixture_for_canonical_suite_runs(self):
        now = timezone.now()
        scenario = GlobalSkillEvalScenario()
        recorded = []
        injected_kwargs = []
        tool_call = SimpleNamespace(
            tool_name="http_request",
            tool_params={"url": "https://api.weather.gov/gridpoints/LWX/96,70/forecast"},
            status="complete",
            step=SimpleNamespace(created_at=now),
        )

        with (
            patch.object(
                scenario,
                "get_run",
                return_value=SimpleNamespace(
                    suite_run=SimpleNamespace(
                        launch_config={},
                        launcher_type=EvalSuiteRun.LauncherType.SUITE,
                    )
                ),
            ),
            patch.object(scenario, "wait_for_agent_idle", return_value=nullcontext()),
            patch.object(
                scenario,
                "inject_message",
                side_effect=lambda *args, **kwargs: injected_kwargs.append(kwargs) or SimpleNamespace(timestamp=now),
            ),
            patch.object(
                scenario,
                "record_task_result",
                side_effect=lambda *args, **kwargs: recorded.append(
                    {
                        "task_name": kwargs.get("task_name"),
                        "status": args[2],
                        "observed_summary": kwargs.get("observed_summary", ""),
                    }
                ),
            ),
            patch.object(scenario, "llm_judge", return_value=("Pass", "Default eval skill was used.")),
            patch("api.evals.scenarios.global_skill_eval.PersistentAgentSkill.objects.filter") as mock_enabled_filter,
            patch("api.evals.scenarios.global_skill_eval.PersistentAgentToolCall.objects.filter") as mock_tool_filter,
            patch("api.evals.scenarios.global_skill_eval.PersistentAgentMessage.objects.filter") as mock_message_filter,
        ):
            mock_enabled_filter.return_value = _FakeQuerySet([SimpleNamespace(name="eval-weather-http-skill")])
            mock_tool_filter.side_effect = [
                _FakeQuerySet([tool_call]),
                _FakeQuerySet([tool_call]),
            ]
            mock_message_filter.return_value = _FakeQuerySet([SimpleNamespace(body="It is 72F and Sunny.")])

            scenario.run("run-1", "agent-1")

        final_status_by_task = {item["task_name"]: item["status"] for item in recorded}
        self.assertEqual(final_status_by_task["judge_skill_execution"], "passed")
        http_mock = injected_kwargs[0]["mock_config"]["http_request"]
        self.assertIn("rules", http_mock)
        self.assertEqual(http_mock["rules"][0]["url_contains"], "geocoding-api.open-meteo.com")
        self.assertTrue(
            any(rule["url_contains"] == "api.weather.gov/points" for rule in http_mock["rules"])
        )
        self.assertTrue(
            any(rule["url_contains"] == "api.weather.gov/gridpoints" for rule in http_mock["rules"])
        )
        self.assertEqual(http_mock["default"]["status"], "error")
        skill = GlobalAgentSkill.objects.get(name="eval-weather-http-skill")
        self.assertIn("forecast or current-conditions endpoint", skill.instructions)

    def test_scenario_fails_when_skill_not_enabled_even_if_judge_passes(self):
        tool_call = SimpleNamespace(
            tool_name="weather",
            tool_params={"location": "Boston"},
            status="complete",
            step=SimpleNamespace(created_at=timezone.now()),
        )

        recorded = self._run_scenario(
            enabled_skill=None,
            relevant_tool_calls=[tool_call],
            judge_result=("Pass", "The answer is correct."),
        )

        self.assertEqual(recorded[-1]["task_name"], "judge_skill_execution")
        self.assertEqual(recorded[-1]["status"], "failed")
        self.assertIn("skill was not enabled", recorded[-1]["observed_summary"])

    def test_scenario_fails_when_no_effective_skill_tool_is_used(self):
        recorded = self._run_scenario(
            enabled_skill=SimpleNamespace(name="check-weather"),
            relevant_tool_calls=[],
            judge_result=("Pass", "The answer looks correct."),
        )

        self.assertEqual(recorded[-1]["task_name"], "judge_skill_execution")
        self.assertEqual(recorded[-1]["status"], "failed")
        self.assertIn("no effective skill tool was used", recorded[-1]["observed_summary"])
