from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.scenarios.telegram_native import (
    FORBIDDEN_TELEGRAM_LEGACY_TOOL_NAMES,
    TELEGRAM_CHAT_BINDING_ID,
    TELEGRAM_NATIVE_CASES,
    TELEGRAM_NATIVE_FORBIDS_LEGACY_SETUP,
    TELEGRAM_NATIVE_GROUP_PRIVACY,
    TELEGRAM_NATIVE_MISSING_CONNECTION,
    TELEGRAM_NATIVE_SCENARIO_SLUGS,
    TELEGRAM_NATIVE_SEND_MESSAGE,
    TELEGRAM_NATIVE_STATUS,
    TELEGRAM_NATIVE_SUITE_SLUG,
    TelegramToolExpectation,
    call_matches_expectation,
)
from api.evals.registry import ScenarioRegistry
from api.evals.suites import SuiteRegistry
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentTelegramBotIdentity,
    PersistentAgentTelegramChatBinding,
)


User = get_user_model()


@tag("eval_sim")
class TelegramNativeScenarioTests(SimpleTestCase):
    def test_telegram_native_suite_contains_checklist_scenarios(self):
        suite = SuiteRegistry.get(TELEGRAM_NATIVE_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), TELEGRAM_NATIVE_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 5)
        self.assertIn(TELEGRAM_NATIVE_STATUS, suite.scenario_slugs)
        self.assertIn(TELEGRAM_NATIVE_SEND_MESSAGE, suite.scenario_slugs)
        self.assertIn(TELEGRAM_NATIVE_MISSING_CONNECTION, suite.scenario_slugs)
        self.assertIn(TELEGRAM_NATIVE_FORBIDS_LEGACY_SETUP, suite.scenario_slugs)
        self.assertIn(TELEGRAM_NATIVE_GROUP_PRIVACY, suite.scenario_slugs)

    def test_generated_scenarios_have_expected_metadata(self):
        registered = ScenarioRegistry.list_all()

        for slug in TELEGRAM_NATIVE_SCENARIO_SLUGS:
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.category, "telegram_native")
            self.assertEqual(metadata.area, "system_skills")
            self.assertEqual(metadata.expected_runtime, "short")
            self.assertEqual(metadata.cost_class, "low")
            self.assertIn("telegram_native", metadata.tags)
            self.assertIn("system_skill", metadata.tags)
            self.assertIn("managed_bot", metadata.tags)

    def test_cases_mock_only_dedicated_telegram_tools(self):
        allowed_mocked_tools = {"telegram_chats", "send_telegram_message"}

        for case in TELEGRAM_NATIVE_CASES:
            self.assertTrue(case.expected_tool_calls)
            self.assertLessEqual(set(case.mock_config()), allowed_mocked_tools)
            for tool_name, mock in case.mock_config().items():
                self.assertIn(tool_name, allowed_mocked_tools)
                self.assertIn("rules", mock)
                self.assertIn("default", mock)

    def test_cases_do_not_prompt_for_legacy_or_raw_api_paths(self):
        for case in TELEGRAM_NATIVE_CASES:
            for expectation in case.expected_tool_calls:
                self.assertIn(expectation.tool_name, {"telegram_chats", "send_telegram_message"})

            prompt_and_description = f"{case.prompt} {case.description}"
            self.assertNotIn("api.telegram.org", prompt_and_description)
            for tool_name in FORBIDDEN_TELEGRAM_LEGACY_TOOL_NAMES:
                self.assertNotIn(tool_name, prompt_and_description)

    def test_eval_stop_policy_forbids_legacy_paths(self):
        scenario = ScenarioRegistry.get(TELEGRAM_NATIVE_STATUS)
        policy = scenario._eval_stop_policy()

        self.assertIn("telegram_chats", policy["allowed_tool_names"])
        self.assertIn("send_telegram_message", policy["allowed_tool_names"])
        self.assertIn("sqlite_batch", policy["allowed_tool_names"])
        self.assertNotIn("http_request", policy["allowed_tool_names"])
        for tool_name in FORBIDDEN_TELEGRAM_LEGACY_TOOL_NAMES:
            self.assertIn(tool_name, policy["stop_on_tool_names"])

    def test_expected_tool_call_requires_completed_status_and_params(self):
        expectation = TelegramToolExpectation(
            name="send_group_message",
            tool_name="send_telegram_message",
            param_equals={"chat_binding_id": TELEGRAM_CHAT_BINDING_ID},
            param_contains={"message": ("standup starts in 10 minutes",)},
        )
        pending_call = SimpleNamespace(
            tool_name="send_telegram_message",
            status="pending",
            tool_params={
                "chat_binding_id": TELEGRAM_CHAT_BINDING_ID,
                "message": "Standup starts in 10 minutes.",
            },
        )
        wrong_body_call = SimpleNamespace(
            tool_name="send_telegram_message",
            status="complete",
            tool_params={
                "chat_binding_id": TELEGRAM_CHAT_BINDING_ID,
                "message": "Different body",
            },
        )
        complete_call = SimpleNamespace(
            tool_name="send_telegram_message",
            status="complete",
            tool_params={
                "chat_binding_id": TELEGRAM_CHAT_BINDING_ID,
                "message": "Standup starts in 10 minutes.",
            },
        )

        self.assertFalse(call_matches_expectation(pending_call, expectation))
        self.assertFalse(call_matches_expectation(wrong_body_call, expectation))
        self.assertTrue(call_matches_expectation(complete_call, expectation))


@tag("eval_sim")
class TelegramNativeScenarioConnectionSeedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="telegram-native-eval",
            email="telegram-native-eval@example.com",
            password="password123",
        )
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Telegram Native Eval Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Telegram Native Eval Agent",
            charter="native telegram eval",
            browser_use_agent=browser_agent,
        )

    def test_connected_native_eval_seeds_managed_bot_and_chat_binding(self):
        scenario = ScenarioRegistry.get(TELEGRAM_NATIVE_STATUS)

        scenario._prepare_agent(str(self.agent.id))

        identity = PersistentAgentTelegramBotIdentity.objects.get(agent=self.agent)
        self.assertEqual(identity.status, PersistentAgentTelegramBotIdentity.Status.ACTIVE)
        self.assertTrue(identity.username.startswith("gobii_eval_"))
        self.assertTrue(identity.username.endswith("_bot"))
        binding = PersistentAgentTelegramChatBinding.objects.get(agent=self.agent)
        self.assertEqual(binding.agent, self.agent)
        self.assertEqual(binding.title, "Ops Group")
        self.assertEqual(binding.status, PersistentAgentTelegramChatBinding.Status.ACTIVE)

    def test_missing_connection_eval_does_not_seed_managed_bot(self):
        scenario = ScenarioRegistry.get(TELEGRAM_NATIVE_MISSING_CONNECTION)

        scenario._prepare_agent(str(self.agent.id))

        self.assertFalse(PersistentAgentTelegramBotIdentity.objects.filter(agent=self.agent).exists())
        self.assertFalse(PersistentAgentTelegramChatBinding.objects.filter(agent=self.agent).exists())
