import json
from dataclasses import dataclass

from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from api.agent.tools.sqlite_state import agent_sqlite_db
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.behavior_micro import get_tool_calls_for_run
from api.evals.scenarios.effort_calibration import _outbound_messages_after
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)


CAPABILITY_ROUTING_SUITE_SLUG = "capability_routing"
CAPABILITY_ROUTE_EXISTING_DIRECT = "capability_route_existing_direct"
CAPABILITY_ROUTE_AMBIGUOUS_SETUP = "capability_route_ambiguous_setup"
CAPABILITY_ROUTING_SCENARIO_SLUGS = (
    CAPABILITY_ROUTE_EXISTING_DIRECT,
    CAPABILITY_ROUTE_AMBIGUOUS_SETUP,
)

DIRECT_TOOL = "eval_direct_metrics_query"
CONNECTED_TOOL = "eval_connected_metrics_query"
RESOURCE_ID = "properties/271828"
CREDENTIAL_FILE_PATH = "/workspace/credentials/product-metrics.json"


@dataclass(frozen=True)
class CapabilityRoutingCase:
    slug: str
    description: str
    prompt: str
    should_query_metric: bool


CAPABILITY_ROUTING_CASES = (
    CapabilityRoutingCase(
        slug=CAPABILITY_ROUTE_EXISTING_DIRECT,
        description="A clear metric request should use the verified durable direct route before a disconnected connector.",
        prompt="Can you pull yesterday's signup count from the product analytics setup we already use?",
        should_query_metric=True,
    ),
    CapabilityRoutingCase(
        slug=CAPABILITY_ROUTE_AMBIGUOUS_SETUP,
        description="An ambiguous analytics request should inspect existing route truth before suggesting setup or reconnect work.",
        prompt="Is our product analytics setup ready to use? I haven't decided what I want to query yet.",
        should_query_metric=False,
    ),
)


def _result_succeeded(call):
    try:
        result = call.result if isinstance(call.result, dict) else json.loads(call.result or "{}")
    except (TypeError, ValueError):
        return False
    return call.status == "complete" and result.get("status") == "ok"


class CapabilityRoutingScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "tool_choice"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "tool_choice", "sqlite", "integration", "real_harness")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_route_state_used", assertion_type="tool_call"),
        ScenarioTask(name="verify_first_route", assertion_type="tool_call"),
    ]

    def __init__(self, case):
        self.case = case
        self.slug = case.slug
        self.description = case.description

    @staticmethod
    def _ready_agent(agent_id):
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Help the owner with product analytics while preserving configured access.",
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        if not PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            step = PersistentAgentStep.objects.create(agent_id=agent_id, description="Process events")
            PersistentAgentSystemStep.objects.create(
                step=step,
                code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            )
        agent = PersistentAgent.objects.get(id=agent_id)
        for tool_name in (DIRECT_TOOL, CONNECTED_TOOL):
            mark_tool_enabled_without_discovery(agent, tool_name)
            PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name=tool_name,
            ).update(
                tool_server=EVAL_SYNTHETIC_TOOL_SERVER,
                tool_name=tool_name,
            )

    @staticmethod
    def _seed_route_state(agent_id):
        with agent_sqlite_db(str(agent_id)) as db_path:
            connection = open_guarded_sqlite_connection(db_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE integration_routes (
                        capability TEXT PRIMARY KEY,
                        resource_id TEXT NOT NULL,
                        credential_file_path TEXT NOT NULL,
                        direct_ready INTEGER NOT NULL,
                        connected_app_ready INTEGER NOT NULL,
                        last_verified_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO integration_routes (
                        capability,
                        resource_id,
                        credential_file_path,
                        direct_ready,
                        connected_app_ready,
                        last_verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "product_metrics",
                        RESOURCE_ID,
                        CREDENTIAL_FILE_PATH,
                        1,
                        0,
                        "2026-07-30T12:00:00Z",
                    ),
                )
                connection.commit()
            finally:
                clear_guarded_connection(connection)
                connection.close()

    def run(self, run_id, agent_id):
        self._ready_agent(agent_id)
        self._seed_route_state(agent_id)
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                self.case.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config={
                    DIRECT_TOOL: {
                        "status": "ok",
                        "metric": "signups",
                        "period": "yesterday",
                        "value": 42,
                    },
                    CONNECTED_TOOL: {
                        "status": "action_required",
                        "message": "No connected-app account is configured.",
                    },
                },
                eval_stop_policy={
                    "allowed_tool_names": [
                        "sqlite_batch",
                        DIRECT_TOOL,
                        CONNECTED_TOOL,
                        "send_chat_message",
                        "request_human_input",
                        "secure_credentials_request",
                        "search_tools",
                    ],
                    "stop_on_tool_names_after_finish": ["send_chat_message"],
                    "max_relevant_tool_calls": 7,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Analytics request completed through the real agent harness.",
            artifacts={"message": inbound},
        )

        calls = get_tool_calls_for_run(run_id, after=inbound.timestamp)
        sqlite_calls = [call for call in calls if call.tool_name == "sqlite_batch"]
        route_calls = [call for call in calls if call.tool_name in {DIRECT_TOOL, CONNECTED_TOOL}]
        first_route_index = calls.index(route_calls[0]) if route_calls else len(calls)
        route_reads = [
            call
            for call in sqlite_calls
            if "integration_routes" in str((call.tool_params or {}).get("sql") or "").casefold()
            and calls.index(call) < first_route_index
            and _result_succeeded(call)
        ]
        exact_direct_params = False
        if route_calls and route_calls[0].tool_name == DIRECT_TOOL:
            first_params = route_calls[0].tool_params or {}
            exact_direct_params = (
                first_params.get("resource_id") == RESOURCE_ID
                and first_params.get("credential_file_path") == CREDENTIAL_FILE_PATH
            )
        route_state_used = len(route_reads) == 1 or (
            self.case.should_query_metric and exact_direct_params and not route_reads
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if route_state_used else EvalRunTask.Status.FAILED,
            task_name="verify_route_state_used",
            observed_summary=(
                "Used exact durable route state without a redundant or partial lookup."
                if route_state_used
                else (
                    "Expected one complete pre-action integration_routes read, or exact direct parameters for the "
                    f"clear metric request; observed reads={len(route_reads)}, exact_direct_params={exact_direct_params}."
                )
            ),
            artifacts={"step": (route_reads or sqlite_calls)[0].step} if route_reads or sqlite_calls else {},
        )

        forbidden = [
            call
            for call in calls
            if call.tool_name in {
                CONNECTED_TOOL,
                "read_file",
                "request_human_input",
                "secure_credentials_request",
                "search_tools",
            }
        ]
        messages = _outbound_messages_after(agent_id, inbound.timestamp)
        if self.case.should_query_metric:
            direct_calls = [call for call in route_calls if call.tool_name == DIRECT_TOOL]
            params = direct_calls[0].tool_params if direct_calls else {}
            passed = (
                len(route_calls) == 1
                and len(direct_calls) == 1
                and _result_succeeded(direct_calls[0])
                and params.get("resource_id") == RESOURCE_ID
                and params.get("credential_file_path") == CREDENTIAL_FILE_PATH
                and not forbidden
            )
            detail = (
                f"route_calls={[call.tool_name for call in route_calls]}, "
                f"resource_id={params.get('resource_id')!r}, "
                f"credential_path={params.get('credential_file_path')!r}, "
                f"forbidden={[call.tool_name for call in forbidden]}"
            )
        else:
            body = "\n".join(message.body or "" for message in messages)
            choice, reasoning = self.llm_judge(
                question=(
                    "Does this response recognize that a working direct product-analytics route already exists and "
                    "ask what the user wants to measure or do, without claiming setup, credentials, connection, or "
                    "reconnection is required?"
                ),
                context=f"User request:\n{self.case.prompt}\n\nAgent response:\n{body}",
                options=["Correctly uses existing route context", "Incorrectly requires setup or misses the route"],
            )
            passed = not route_calls and not forbidden and choice == "Correctly uses existing route context"
            detail = (
                f"route_calls={[call.tool_name for call in route_calls]}, "
                f"forbidden={[call.tool_name for call in forbidden]}, judge={choice}: {reasoning}"
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if passed else EvalRunTask.Status.FAILED,
            task_name="verify_first_route",
            observed_summary=(
                "Used verified existing capability state without a connector detour."
                if passed
                else detail
            ),
            artifacts={"step": (route_calls or forbidden or route_reads)[0].step}
            if route_calls or forbidden or route_reads
            else {},
        )


for capability_routing_case in CAPABILITY_ROUTING_CASES:
    ScenarioRegistry.register(CapabilityRoutingScenario(capability_routing_case))
