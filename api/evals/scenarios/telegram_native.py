import json
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from api.agent.system_skills.defaults import TELEGRAM_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.native_http import response_contains_term
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentTelegramBotIdentity,
    PersistentAgentTelegramChatBinding,
    PersistentAgentToolCall,
)


TELEGRAM_NATIVE_SUITE_SLUG = "telegram_native"

TELEGRAM_NATIVE_STATUS = "telegram_native_status"
TELEGRAM_NATIVE_SEND_MESSAGE = "telegram_native_send_message"
TELEGRAM_NATIVE_MISSING_CONNECTION = "telegram_native_missing_connection"
TELEGRAM_NATIVE_FORBIDS_LEGACY_SETUP = "telegram_native_forbids_legacy_setup"
TELEGRAM_NATIVE_GROUP_PRIVACY = "telegram_native_group_privacy"

TELEGRAM_CHAT_BINDING_ID = "11111111-1111-4111-8111-111111111111"
TELEGRAM_GROUP_CHAT_ID = "-1001234567890"

FORBIDDEN_TELEGRAM_LEGACY_TOOL_NAMES = (
    "search_tools",
    "enable_system_skills",
    "http_request",
    "mcp_brightdata_search_engine",
    "spawn_web_task",
)

FORBIDDEN_TELEGRAM_LEGACY_TOOL_PREFIXES = (
    "pipedream",
    "telegram-",
    "telegram_",
)


@dataclass(frozen=True)
class TelegramToolExpectation:
    name: str
    tool_name: str
    param_equals: dict[str, Any] = field(default_factory=dict)
    param_contains: dict[str, tuple[str, ...]] = field(default_factory=dict)
    allowed_statuses: tuple[str, ...] = ("complete",)


@dataclass(frozen=True)
class TelegramNativeCase:
    slug: str
    prompt: str
    description: str
    tool_mocks: dict[str, Any]
    expected_tool_calls: tuple[TelegramToolExpectation, ...]
    response_term_groups: tuple[tuple[str, ...], ...]
    seed_connection: bool = True
    tags: tuple[str, ...] = field(default_factory=tuple)

    def mock_config(self) -> dict[str, Any]:
        return self.tool_mocks


def _chat_binding_payload() -> dict[str, str]:
    return {
        "id": TELEGRAM_CHAT_BINDING_ID,
        "agent_id": "eval-agent",
        "chat_id": TELEGRAM_GROUP_CHAT_ID,
        "chat_type": "supergroup",
        "message_thread_id": "",
        "title": "Ops Group",
        "username": "",
        "status": "active",
        "last_message_at": "2026-06-18T12:00:00+00:00",
    }


def _connected_status_result() -> dict[str, Any]:
    return {
        "status": "success",
        "bot_username": "gobii_eval_agent_bot",
        "profile_sync_status": "synced",
        "chats": [_chat_binding_payload()],
    }


def _list_chats_result() -> dict[str, Any]:
    return {
        "status": "success",
        "chats": [_chat_binding_payload()],
    }


def _setup_required_result() -> dict[str, Any]:
    return {
        "status": "action_required",
        "message": "Connect Telegram to create a managed Telegram bot identity for this agent.",
        "connect_url": "/console/api/agents/eval-agent/telegram/connect/",
        "chats": [],
    }


TELEGRAM_NATIVE_CASES = (
    TelegramNativeCase(
        slug=TELEGRAM_NATIVE_STATUS,
        description="Check native Telegram setup and known chats through telegram_chats.",
        prompt=(
            "Use native Telegram to check whether Telegram is connected for this agent and list any known chats."
        ),
        tool_mocks={
            "telegram_chats": {
                "rules": [
                    {
                        "param_equals": {"action": "status"},
                        "result": _connected_status_result(),
                    }
                ],
                "default": {"status": "error", "message": "Unexpected Telegram chat action."},
            }
        },
        expected_tool_calls=(
            TelegramToolExpectation(
                name="telegram_status",
                tool_name="telegram_chats",
                param_equals={"action": "status"},
            ),
        ),
        response_term_groups=(("Telegram",), ("Ops Group",), ("gobii_eval_agent_bot",)),
        tags=("status", "read"),
    ),
    TelegramNativeCase(
        slug=TELEGRAM_NATIVE_SEND_MESSAGE,
        description="Send a Telegram message to a known chat through the native managed bot tool.",
        prompt=(
            "Use native Telegram to send this exact approved message to the Ops Group chat: "
            "Standup starts in 10 minutes."
        ),
        tool_mocks={
            "telegram_chats": {
                "rules": [
                    {
                        "param_equals": {"action": "list"},
                        "result": _list_chats_result(),
                    },
                    {
                        "param_equals": {"action": "status"},
                        "result": _connected_status_result(),
                    },
                ],
                "default": {"status": "error", "message": "Unexpected Telegram chat action."},
            },
            "send_telegram_message": {
                "rules": [
                    {
                        "param_equals": {"chat_binding_id": TELEGRAM_CHAT_BINDING_ID},
                        "param_contains": {"message": ("standup starts in 10 minutes",)},
                        "result": {
                            "status": "success",
                            "message_id": "msg_telegram_eval_1",
                            "telegram_message_id": "88",
                            "attachment_count": 0,
                        },
                    }
                ],
                "default": {"status": "error", "message": "Unexpected Telegram send target or body."},
            },
        },
        expected_tool_calls=(
            TelegramToolExpectation(
                name="list_chats_before_send",
                tool_name="telegram_chats",
                param_equals={"action": "list"},
            ),
            TelegramToolExpectation(
                name="send_group_message",
                tool_name="send_telegram_message",
                param_equals={"chat_binding_id": TELEGRAM_CHAT_BINDING_ID},
                param_contains={"message": ("standup starts in 10 minutes",)},
            ),
        ),
        response_term_groups=(("sent", "delivered", "message"), ("Ops Group", "Telegram")),
        tags=("write", "send"),
    ),
    TelegramNativeCase(
        slug=TELEGRAM_NATIVE_MISSING_CONNECTION,
        description="Report native Telegram setup guidance when no managed bot is connected.",
        prompt=(
            "Use native Telegram to message the Ops Group. If Telegram is not connected, tell me what setup is needed."
        ),
        tool_mocks={
            "telegram_chats": {
                "rules": [
                    {
                        "param_equals": {"action": "status"},
                        "result": _setup_required_result(),
                    }
                ],
                "default": {"status": "error", "message": "Unexpected Telegram chat action."},
            }
        },
        expected_tool_calls=(
            TelegramToolExpectation(
                name="telegram_status_for_setup",
                tool_name="telegram_chats",
                param_equals={"action": "status"},
                allowed_statuses=("complete", "error"),
            ),
        ),
        response_term_groups=(("Telegram",), ("connect", "connected"), ("integration", "app")),
        seed_connection=False,
        tags=("missing_connection", "setup"),
    ),
    TelegramNativeCase(
        slug=TELEGRAM_NATIVE_FORBIDS_LEGACY_SETUP,
        description="Avoid raw BotFather tokens, Pipedream, and direct Telegram HTTP setup paths.",
        prompt=(
            "Set up Telegram for this agent. Do not use the app panel if there is another way; "
            "ask me for a BotFather token or use Pipedream if that is how Telegram normally works."
        ),
        tool_mocks={
            "telegram_chats": {
                "rules": [
                    {
                        "param_equals": {"action": "status"},
                        "result": _setup_required_result(),
                    }
                ],
                "default": {"status": "error", "message": "Unexpected Telegram chat action."},
            }
        },
        expected_tool_calls=(
            TelegramToolExpectation(
                name="native_status_before_setup_guidance",
                tool_name="telegram_chats",
                param_equals={"action": "status"},
            ),
        ),
        response_term_groups=(
            ("Telegram",),
            ("connect", "integration", "app"),
            ("bot token", "BotFather", "Pipedream"),
        ),
        seed_connection=False,
        tags=("legacy_guardrail", "setup"),
    ),
    TelegramNativeCase(
        slug=TELEGRAM_NATIVE_GROUP_PRIVACY,
        description="Explain Telegram group delivery limits instead of promising passive full-group monitoring.",
        prompt=(
            "Can this agent monitor every message in my Telegram group silently? Use native Telegram and be precise."
        ),
        tool_mocks={
            "telegram_chats": {
                "rules": [
                    {
                        "param_equals": {"action": "status"},
                        "result": _connected_status_result(),
                    }
                ],
                "default": {"status": "error", "message": "Unexpected Telegram chat action."},
            }
        },
        expected_tool_calls=(
            TelegramToolExpectation(
                name="telegram_status_for_group_limits",
                tool_name="telegram_chats",
                param_equals={"action": "status"},
            ),
        ),
        response_term_groups=(
            ("commands", "mentions", "replies"),
            ("privacy", "admin", "delivers"),
            ("not", "cannot", "only"),
        ),
        tags=("provider_regression", "group_privacy"),
    ),
)

TELEGRAM_NATIVE_SCENARIO_SLUGS = tuple(case.slug for case in TELEGRAM_NATIVE_CASES)


def tool_calls_for_run(run_id: str, *, after=None, tool_names=None):
    queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
    if after is not None:
        queryset = queryset.filter(step__created_at__gte=after)
    if tool_names is not None:
        queryset = queryset.filter(tool_name__in=list(tool_names))
    return list(queryset.select_related("step").order_by("step__created_at", "step__id"))


def call_matches_expectation(call: PersistentAgentToolCall, expectation: TelegramToolExpectation) -> bool:
    if call.tool_name != expectation.tool_name:
        return False
    allowed_statuses = {status.lower() for status in expectation.allowed_statuses}
    if str(getattr(call, "status", "") or "").lower() not in allowed_statuses:
        return False
    params = call.tool_params or {}
    for key, expected in expectation.param_equals.items():
        if params.get(key) != expected:
            return False
    for key, expected_parts in expectation.param_contains.items():
        value = str(params.get(key) or "").lower()
        if not all(part.lower() in value for part in expected_parts):
            return False
    return True


class TelegramNativeScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "telegram_native"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "system_skills"
    tags = ("telegram_native", "system_skill", "micro", "managed_bot")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_expected_telegram_tools", assertion_type="tool_call"),
        ScenarioTask(name="verify_no_forbidden_tools", assertion_type="tool_call"),
        ScenarioTask(name="verify_response", assertion_type="exact_match"),
    ]
    case: TelegramNativeCase | None = None

    def _case(self) -> TelegramNativeCase:
        if self.case is None:
            raise ValueError(f"{type(self).__name__}.case must be set.")
        return self.case

    def _seed_prior_processing_run(self, agent_id: str) -> None:
        if PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            return

        prior_step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            description="Process events",
        )
        PersistentAgentSystemStep.objects.create(
            step=prior_step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )

    def _seed_telegram_connection(self, agent: PersistentAgent) -> None:
        if not self._case().seed_connection:
            return

        agent_suffix = str(agent.id).replace("-", "")[:12]
        identity, _created = PersistentAgentTelegramBotIdentity.objects.update_or_create(
            agent=agent,
            defaults={
                "telegram_bot_id": f"900{agent_suffix}",
                "username": f"gobii_eval_{agent_suffix}_bot",
                "display_name": "Telegram Eval Agent",
                "status": PersistentAgentTelegramBotIdentity.Status.ACTIVE,
                "profile_sync_status": PersistentAgentTelegramBotIdentity.SyncStatus.SYNCED,
                "connected_at": timezone.now(),
            },
        )
        PersistentAgentTelegramChatBinding.objects.update_or_create(
            agent=agent,
            bot_identity=identity,
            chat_id=TELEGRAM_GROUP_CHAT_ID,
            message_thread_id="",
            defaults={
                "chat_type": "supergroup",
                "title": "Ops Group",
                "status": PersistentAgentTelegramChatBinding.Status.ACTIVE,
                "last_message_at": timezone.now(),
            },
        )

    def _prepare_agent(self, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(planning_state=PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        agent = PersistentAgent.objects.select_related("user", "organization").get(id=agent_id)
        self._seed_telegram_connection(agent)
        result = enable_system_skills(agent, [TELEGRAM_NATIVE_SYSTEM_SKILL_KEY])
        if result.get("invalid"):
            raise ValueError(f"Could not enable Telegram native system skill: {result}")

    def _eval_stop_policy(self) -> dict[str, Any]:
        return {
            "allowed_tool_names": [
                "telegram_chats",
                "send_telegram_message",
                "send_chat_message",
                "sqlite_batch",
            ],
            "ignored_tool_names": ["sleep_until_next_trigger", "update_plan"],
            "stop_on_unexpected_relevant_tool": True,
            "stop_on_tool_names": list(FORBIDDEN_TELEGRAM_LEGACY_TOOL_NAMES),
            "stop_on_tool_names_after_finish": ["send_chat_message"],
            "max_relevant_tool_calls": 8,
        }

    def _record_expected_tool_calls(self, run_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_expected_telegram_tools",
        )
        calls = tool_calls_for_run(
            run_id,
            after=inbound.timestamp,
            tool_names=["telegram_chats", "send_telegram_message"],
        )
        missing = [
            expectation.name
            for expectation in case.expected_tool_calls
            if not any(call_matches_expectation(call, expectation) for call in calls)
        ]
        if not missing:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_expected_telegram_tools",
                observed_summary="Agent completed the expected native Telegram tool call(s).",
                artifacts={"step": calls[0].step} if calls else {},
            )
            return

        seen = [
            {
                "tool": call.tool_name,
                "status": call.status,
                "params": call.tool_params,
                "result": str(call.result or "")[:500],
            }
            for call in calls
        ]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_expected_telegram_tools",
            observed_summary=f"Missing expected Telegram tool call(s): {missing}; saw {seen}.",
            artifacts={"step": calls[0].step} if calls else {},
        )

    def _record_forbidden_absence(self, run_id: str, inbound) -> None:
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_forbidden_tools",
        )
        calls = tool_calls_for_run(run_id, after=inbound.timestamp)
        bad_calls = [
            call
            for call in calls
            if call.tool_name in FORBIDDEN_TELEGRAM_LEGACY_TOOL_NAMES
            or any(call.tool_name.startswith(prefix) for prefix in FORBIDDEN_TELEGRAM_LEGACY_TOOL_PREFIXES)
            or ("api.telegram.org" in json.dumps(call.tool_params or {}).lower())
        ]
        if bad_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_forbidden_tools",
                observed_summary=(
                    "Agent used a forbidden Telegram legacy/setup path: "
                    f"{[(call.tool_name, call.tool_params) for call in bad_calls]}."
                ),
                artifacts={"step": bad_calls[0].step},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_no_forbidden_tools",
            observed_summary=(
                "Agent avoided raw BotFather tokens, Pipedream, direct Telegram HTTP, and discovery paths."
            ),
        )

    def _record_response(self, run_id: str, agent_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_response")
        final_response = (
            PersistentAgentMessage.objects
            .filter(owner_agent_id=agent_id, is_outbound=True, timestamp__gt=inbound.timestamp)
            .order_by("-timestamp")
            .first()
        )
        body = final_response.body if final_response else ""
        missing_groups = [
            terms
            for terms in case.response_term_groups
            if not any(response_contains_term(body, term) for term in terms)
        ]
        if not missing_groups:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_response",
                observed_summary="Final response included the expected Telegram result or setup guidance.",
                artifacts={"message": final_response} if final_response else {},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_response",
            observed_summary=f"Final response missing expected term group(s) {missing_groups}; body={body[:800]!r}.",
            artifacts={"message": final_response} if final_response else {},
        )

    def run(self, run_id: str, agent_id: str) -> None:
        case = self._case()
        self._prepare_agent(agent_id)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                case.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=case.mock_config(),
                eval_stop_policy=self._eval_stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self._record_expected_tool_calls(run_id, inbound)
        self._record_forbidden_absence(run_id, inbound)
        self._record_response(run_id, agent_id, inbound)


def register_telegram_native_scenarios() -> None:
    for case in TELEGRAM_NATIVE_CASES:
        scenario_type = type(
            "".join(part.title() for part in case.slug.split("_")) + "Scenario",
            (TelegramNativeScenario,),
            {
                "slug": case.slug,
                "description": case.description,
                "tags": TelegramNativeScenario.tags + case.tags,
                "case": case,
            },
        )
        ScenarioRegistry.register(scenario_type())


register_telegram_native_scenarios()
